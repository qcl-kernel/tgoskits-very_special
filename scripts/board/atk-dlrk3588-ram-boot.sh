#!/usr/bin/env bash
# RAM-only boot of a FIT or legacy uImage on the ATK-DLRK3588 (RK3588) board.
#
# The vendor U-Boot on this board ships with `bootdelay=0`, so the only way to
# reach its `=>` prompt is to flood the console with Ctrl-C across the whole
# window between a reset and the first autoboot. Doing that by hand loses the
# race whenever the reset and the flood are issued as two separate steps, which
# is why this script binds them together: the flood is already running before
# the reboot is triggered, and it keeps running until the prompt is observed.
#
# The board is never written to. Only `fastboot stage` is used, which parks the
# image in the download buffer; no `flash`, `erase`, or `gpt` command is issued
# and no eMMC partition is touched. A power cycle returns the board to its
# vendor system.
#
# Vendor U-Boot quirks this flow works around:
#
#   * `fastboot boot` is parsed as an Android boot image and trips a sysmem
#     overlap check that resets the board. Use `stage` plus a manual `booti`.
#   * `iminfo`, `imxtract`, `hash`, and `sysmem` are not compiled in, so the FIT
#     is unpacked with the `fdt` command and copied with `cp.b`.
#   * The download buffer address is not exported as an environment variable.
#     It is compiled in at 0x00c00800, recovered by parsing the U-Boot binary.
#
# Usage:
#   scripts/board/atk-dlrk3588-ram-boot.sh <image>
#
# Environment:
#   ATK_PORT          serial device (default /dev/ttyACM0)
#   ATK_BAUD          serial baud rate (default 1500000)
#   ATK_FASTBOOT_SN   optional fastboot serial; required when multiple devices exist
#   ATK_FASTBOOT_WAIT seconds to wait for USB fastboot enumeration (default 15)
#   ATK_BOOT_FORMAT   fit or legacy-uimage (default fit)
#   ATK_LEGACY_LOAD_ADDRESS destination address for a legacy-uImage payload
#   ATK_LEGACY_ENTRY_ADDRESS entry address after copying a legacy-uImage payload
#   ATK_LEGACY_PAYLOAD_SIZE hexadecimal byte count for a legacy-uImage payload
#   ATK_BREAK_WINDOW  seconds to wait for an operator reset while flooding Ctrl-C (default 300)
#   ATK_LOG           console capture path (default a mktemp file)
#   ATK_READY_REGEX   optional ERE that must appear after `booti`
#   ATK_READY_TIMEOUT maximum seconds to wait for ATK_READY_REGEX (default 180)
#   ATK_POST_BOOT_BAUD optional serial baud after the kernel handoff
#   ATK_POST_BOOT_CAPTURE seconds to keep capturing after booti (default 0)

set -euo pipefail

readonly FASTBOOT_DOWNLOAD_BUFFER=0x00c00800

port="${ATK_PORT:-/dev/ttyACM0}"
baud="${ATK_BAUD:-1500000}"
fastboot_sn="${ATK_FASTBOOT_SN:-}"
fastboot_wait="${ATK_FASTBOOT_WAIT:-15}"
boot_format="${ATK_BOOT_FORMAT:-fit}"
legacy_load_address="${ATK_LEGACY_LOAD_ADDRESS:-}"
legacy_entry_address="${ATK_LEGACY_ENTRY_ADDRESS:-}"
legacy_payload_size="${ATK_LEGACY_PAYLOAD_SIZE:-}"
break_window="${ATK_BREAK_WINDOW:-300}"
post_boot_capture="${ATK_POST_BOOT_CAPTURE:-0}"
ready_regex="${ATK_READY_REGEX:-}"
ready_timeout="${ATK_READY_TIMEOUT:-180}"
post_boot_baud="${ATK_POST_BOOT_BAUD:-}"
fit_path=""
console_log=""
reader_pid=""
breaker_pid=""
boot_output_mark=""

main() {
    parse_arguments "$@"
    require_tools
    claim_console
    reach_uboot_prompt
    stage_fit_into_ram
    case "$boot_format" in
    fit) boot_fit_from_ram ;;
    legacy-uimage) boot_legacy_uimage_from_ram ;;
    esac
    capture_post_boot
    printf 'booted %s from RAM; console capture: %s\n' "$fit_path" "$console_log"
}

capture_post_boot() {
    if [[ -n "$post_boot_baud" ]]; then
        printf 'switching serial capture to post-boot baud %s\n' "$post_boot_baud"
        sudo -n stty -F "$port" "$post_boot_baud" raw -echo -crtscts
    fi
    if [[ -n "$ready_regex" ]]; then
        printf 'waiting up to %ss for ready marker: %s\n' "$ready_timeout" "$ready_regex"
        if ! wait_for_console "$boot_output_mark" "$ready_regex" "$ready_timeout"; then
            printf 'error: booted image did not reach ready marker within %ss: %s\n' \
                "$ready_timeout" "$ready_regex" >&2
            printf 'error: see console capture: %s\n' "$console_log" >&2
            exit 1
        fi
        printf 'ready marker observed\n'
    fi
    if [[ "$post_boot_capture" != "0" ]]; then
        printf 'capturing the booted system for %ss\n' "$post_boot_capture"
        sleep "$post_boot_capture"
    fi
}

parse_arguments() {
    if [[ $# -ne 1 ]]; then
        printf 'usage: %s <image.fit>\n' "${BASH_SOURCE[0]##*/}" >&2
        exit 2
    fi
    fit_path="$1"
    if [[ ! -f "$fit_path" ]]; then
        printf 'error: boot image not found: %s\n' "$fit_path" >&2
        exit 2
    fi
    case "$boot_format" in
    fit|legacy-uimage) ;;
    *)
        printf 'error: ATK_BOOT_FORMAT must be fit or legacy-uimage: %s\n' \
            "$boot_format" >&2
        exit 2
        ;;
    esac
    if [[ "$boot_format" == "legacy-uimage" ]]; then
        [[ "$legacy_load_address" =~ ^0x[0-9a-fA-F]+$ ]] || {
            printf 'error: ATK_LEGACY_LOAD_ADDRESS must be hexadecimal\n' >&2
            exit 2
        }
        [[ "$legacy_payload_size" =~ ^0x[1-9a-fA-F][0-9a-fA-F]*$ ]] || {
            printf 'error: ATK_LEGACY_PAYLOAD_SIZE must be positive hexadecimal\n' >&2
            exit 2
        }
        [[ "$legacy_entry_address" =~ ^0x[0-9a-fA-F]+$ ]] || {
            printf 'error: ATK_LEGACY_ENTRY_ADDRESS must be hexadecimal\n' >&2
            exit 2
        }
    fi
}

require_tools() {
    local executable
    for executable in fastboot sudo; do
        if ! command -v "$executable" >/dev/null 2>&1; then
            printf 'error: required executable is unavailable: %s\n' "$executable" >&2
            exit 1
        fi
    done
    if [[ ! -e "$port" ]]; then
        printf 'error: serial port not present: %s\n' "$port" >&2
        exit 1
    fi
}

# Takes sole ownership of the console.
#
# A second reader on the same tty steals bytes from this one, which shows up
# later as truncated U-Boot replies and corrupted `fdt` output. Refuse to start
# rather than produce a confusing half-broken session.
claim_console() {
    local holders
    holders="$(sudo -n fuser "$port" 2>/dev/null || true)"
    if [[ -n "${holders// /}" ]]; then
        printf 'error: %s is already open by PID(s):%s\n' "$port" "$holders" >&2
        printf 'error: stop them first; concurrent readers drop console bytes\n' >&2
        exit 1
    fi

    console_log="${ATK_LOG:-$(mktemp -t atk-dlrk3588-console.XXXXXX.log)}"
    sudo -n stty -F "$port" "$baud" raw -echo -crtscts
    sudo -n cat "$port" >>"$console_log" &
    reader_pid=$!
    trap release_console EXIT
    printf 'console capture: %s\n' "$console_log"
}

release_console() {
    stop_break_flood
    if [[ -n "$reader_pid" ]]; then
        sudo -n kill "$reader_pid" 2>/dev/null || true
        wait "$reader_pid" 2>/dev/null || true
        reader_pid=""
    fi
}

# Walks an ordered ladder from whatever state the board is in to a `=>` prompt.
#
# Each rung is cheaper and more reliable than the one below it, and every rung
# that can trigger a reset does so with the Ctrl-C flood already running.
reach_uboot_prompt() {
    if already_at_uboot_prompt; then
        printf 'board is already at the U-Boot prompt\n'
        return
    fi

    local reset_mark reset_source
    if board_in_fastboot; then
        reset_source="fastboot"
    elif board_in_adb; then
        reset_source="vendor-adb"
    elif console_shows_axvisor_shell || detach_from_axvisor_guest_console; then
        reset_source="axvisor"
    elif console_shows_linux_shell; then
        reset_source="vendor-serial"
    else
        reset_source="manual"
    fi

    reset_mark="$(console_mark)"
    start_break_flood
    case "$reset_source" in
    fastboot)
        printf 'board is in fastboot; leaving it with the flood already running\n'
        send_console $'\003'
        ;;
    vendor-adb)
        printf 'board is in vendor Linux (adb); rebooting into U-Boot\n'
        adb reboot >/dev/null 2>&1 || true
        ;;
    axvisor)
        printf 'board is in AxVisor; rebooting into U-Boot\n'
        send_console $'reboot\r'
        ;;
    vendor-serial)
        printf 'board is in vendor Linux (serial root shell); rebooting into U-Boot\n'
        send_console $'reboot\r'
        ;;
    manual)
        printf 'cannot reach the board from the host.\n'
        printf 'BOARD_RESET_REQUIRED: press the physical RST button once now.\n'
        printf 'press the RST button now; the Ctrl-C flood is already running.\n'
        printf 'waiting up to %ss for U-Boot; no manual Ctrl-C input is needed.\n' \
            "$break_window"
        ;;
    esac

    if ! wait_for_console "$reset_mark" '=> *$' "$break_window"; then
        stop_break_flood
        printf 'error: no U-Boot prompt within %ss; see %s\n' "$break_window" "$console_log" >&2
        exit 1
    fi
    stop_break_flood
    printf 'reached the U-Boot prompt\n'
}

already_at_uboot_prompt() {
    local mark
    mark="$(console_mark)"
    send_console $'\r'
    wait_for_console "$mark" '=> *$' 3
}

console_shows_axvisor_shell() {
    local mark
    mark="$(console_mark)"
    send_console $'\r'
    wait_for_console "$mark" 'axvisor:(/)?\$ *$' 3
}

# Returns from an attached guest console to the AxVisor shell.  Trying the
# documented escape is harmless when no guest is attached, and closes the one
# state in which the board is healthy but neither a guest shell prompt nor the
# host prompt reliably identifies who should process `reboot`.
detach_from_axvisor_guest_console() {
    local mark
    mark="$(console_mark)"
    send_console $'\030h'
    wait_for_console "$mark" 'axvisor:(/)?\$ *$' 3
}

board_in_fastboot() {
    if [[ -n "$fastboot_sn" ]]; then
        sudo -n fastboot devices 2>/dev/null | awk '{print $1}' | grep -Fxq "$fastboot_sn"
    else
        [[ -n "$(sudo -n fastboot devices 2>/dev/null)" ]]
    fi
}

resolve_fastboot_serial() {
    local -a serials=()
    local deadline=$((SECONDS + fastboot_wait))
    while :; do
        mapfile -t serials < <(sudo -n fastboot devices 2>/dev/null | awk 'NF >= 2 {print $1}')
        if [[ -n "$fastboot_sn" ]]; then
            if printf '%s\n' "${serials[@]}" | grep -Fxq "$fastboot_sn"; then
                return
            fi
        elif ((${#serials[@]} == 1)); then
            fastboot_sn="${serials[0]}"
            printf 'selected the only fastboot device: %s\n' "$fastboot_sn"
            return
        elif ((${#serials[@]} > 1)); then
            printf 'error: found multiple fastboot devices; set ATK_FASTBOOT_SN\n' >&2
            return 1
        fi
        if ((SECONDS >= deadline)); then
            break
        fi
        sleep 0.25
    done
    if [[ -n "$fastboot_sn" ]]; then
        printf 'error: requested fastboot device did not enumerate within %ss: %s\n' \
            "$fastboot_wait" "$fastboot_sn" >&2
    else
        printf 'error: no fastboot device enumerated within %ss\n' "$fastboot_wait" >&2
    fi
    return 1
}

board_in_adb() {
    command -v adb >/dev/null 2>&1 && adb devices 2>/dev/null | grep -qE '\sdevice$'
}

console_shows_linux_shell() {
    local mark
    mark="$(console_mark)"
    send_console $'\r'
    wait_for_console "$mark" '(#|\$) *$' 3
}

# Floods Ctrl-C until told to stop.
#
# Started before any reset is triggered so there is no window in which autoboot
# can win the race.
start_break_flood() {
    (
        while :; do
            printf '\003' | sudo -n tee "$port" >/dev/null 2>&1 || true
            sleep 0.05
        done
    ) &
    breaker_pid=$!
}

stop_break_flood() {
    if [[ -n "$breaker_pid" ]]; then
        kill "$breaker_pid" 2>/dev/null || true
        wait "$breaker_pid" 2>/dev/null || true
        breaker_pid=""
    fi
}

# Parks the FIT in the U-Boot download buffer. This is the only transfer the
# script performs, and it lands in RAM only.
stage_fit_into_ram() {
    printf 'staging %s (%s bytes) into RAM\n' "$fit_path" "$(stat -c %s "$fit_path")"
    local stage_mark
    stage_mark="$(console_mark)"
    send_console $'fastboot usb 0\r'
    if ! wait_for_console "$stage_mark" 'Enter fastboot' 15; then
        printf 'error: U-Boot did not enter fastboot; see %s\n' "$console_log" >&2
        exit 1
    fi
    resolve_fastboot_serial
    if ! sudo -n fastboot -s "$fastboot_sn" stage "$fit_path"; then
        printf 'error: fastboot stage failed\n' >&2
        exit 1
    fi
    stage_mark="$(console_mark)"
    send_console $'\003'
    if ! wait_for_console "$stage_mark" '=> *$' 15; then
        printf 'error: U-Boot did not return after staging; see %s\n' "$console_log" >&2
        exit 1
    fi
}

# Unpacks the staged FIT and jumps into it.
#
# Addresses and sizes are read back out of the image that is actually in RAM
# rather than assumed from the build, so a stale offset cannot send `cp.b` at
# the wrong region. The device tree is copied before the kernel: the kernel is
# the larger payload and copying it first can overrun the device tree's source
# bytes while they are still needed.
boot_fit_from_ram() {
    send_console "fdt addr $FASTBOOT_DOWNLOAD_BUFFER"$'\r'
    send_uboot_command 'fdt get addr fdtsrc /images/fdt-1 data'
    send_uboot_command 'fdt get size fdtlen /images/fdt-1 data'
    send_uboot_command 'fdt get value fdtdst /images/fdt-1 load'
    send_uboot_command 'fdt get addr kernelsrc /images/kernel-1 data'
    send_uboot_command 'fdt get size kernellen /images/kernel-1 data'
    send_uboot_command 'fdt get value kerneldst /images/kernel-1 load'
    send_uboot_command 'printenv fdtsrc fdtlen fdtdst kernelsrc kernellen kerneldst'

    send_uboot_command 'cp.b ${fdtsrc} ${fdtdst} ${fdtlen}'
    send_uboot_command 'cp.b ${kernelsrc} ${kerneldst} ${kernellen}'
    send_uboot_command 'fdt addr ${fdtdst}'
    send_uboot_command 'fdt header'

    printf 'starting the image from RAM\n'
    boot_output_mark="$(console_mark)"
    send_console 'booti ${kerneldst} - ${fdtdst}'$'\r'
}

# Boots a legacy U-Boot image staged in the download buffer.  The vendor's
# `loados` subcommand aborts while copying this image, and this build does not
# provide `setexpr`.  `bootm start` first verifies the header and data CRC, an
# explicit copy moves the payload past the 64-byte legacy header. The payload
# itself is a standard AArch64 Linux Image (`ARMd` header), so `booti` performs
# the required exception-level handoff. This vendor build aborts inside both
# legacy `bootm loados` and `bootm go`; neither may be used after verification.
boot_legacy_uimage_from_ram() {
    send_uboot_command "bootm start $FASTBOOT_DOWNLOAD_BUFFER"
    send_uboot_command "cp.b 0x00c00840 $legacy_load_address $legacy_payload_size"

    printf 'starting the verified AArch64 Image payload from RAM\n'
    boot_output_mark="$(console_mark)"
    send_console "booti $legacy_load_address"$'\r'
}

# Sends one U-Boot command and waits for the prompt before sending the next.
#
# The console drops characters when commands are pushed back to back at this
# baud rate, so each command is acknowledged before the next one is written.
send_uboot_command() {
    local command="$1" mark
    mark="$(console_mark)"
    send_console "$command"$'\r'
    if ! wait_for_console "$mark" '=> *$' 10; then
        printf 'error: U-Boot did not acknowledge: %s\n' "$command" >&2
        exit 1
    fi
}

send_console() {
    printf '%s' "$1" | sudo -n tee "$port" >/dev/null
}

# Records how much console output exists right now.
#
# Callers must take this mark *before* writing to the board. The board can
# answer faster than the shell reaches the next statement, so a mark taken
# after the write can already sit past the reply.
console_mark() {
    stat -c %s "$console_log" 2>/dev/null || echo 0
}

# Waits for a regex to appear in console output produced after `mark`.
wait_for_console() {
    local mark="$1" pattern="$2" timeout="$3"
    local deadline=$((SECONDS + timeout))
    while ((SECONDS < deadline)); do
        if tail -c "+$((mark + 1))" "$console_log" 2>/dev/null |
            grep -qE "$pattern"; then
            return 0
        fi
        sleep 0.2
    done
    return 1
}

main "$@"
