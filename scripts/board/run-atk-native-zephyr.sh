#!/usr/bin/env bash
# Run the periodic Zephyr sampler directly on the ATK-DLRK3588, without
# AxVisor or another guest, and archive strictly validated physical-board
# evidence.  The image is staged and booted from RAM only.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
input_dir="${NATIVE_ZEPHYR_INPUT_DIR:-$repo_root/results/task1/native-zephyr-board-20260825/build}"
run_stamp="$(date +%Y%m%d-%H%M%S)"
out_dir="${NATIVE_ZEPHYR_OUTPUT_DIR:-$repo_root/results/task1/native-zephyr-board-20260825/run-$run_stamp}"
samples="${NATIVE_ZEPHYR_SAMPLES:-6000}"
timeout_sec="${NATIVE_ZEPHYR_TIMEOUT_SEC:-180}"
zephyr_baud="${NATIVE_ZEPHYR_BAUD:-1500000}"
ram_boot="${ATK_RAM_BOOT_RUNNER:-$repo_root/scripts/board/atk-dlrk3588-ram-boot.sh}"
fdt_load="${NATIVE_ZEPHYR_FDT_LOAD:-0x17f00000}"
input_dir="$(realpath -m "$input_dir")"
out_dir="$(realpath -m "$out_dir")"
host_dtb="${NATIVE_ZEPHYR_DTB:-${ATK_HOST_DTB:-${TASK123_BOARD_DTB:-$input_dir/zephyr-periodic.dtb}}}"

[[ "$samples" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: NATIVE_ZEPHYR_SAMPLES must be a positive integer\n' >&2
    exit 2
}
[[ "$timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: NATIVE_ZEPHYR_TIMEOUT_SEC must be a positive integer\n' >&2
    exit 2
}
for executable in mkimage python3 sha256sum; do
    command -v "$executable" >/dev/null 2>&1 || {
        printf 'error: required executable is unavailable: %s\n' "$executable" >&2
        exit 1
    }
done
[[ -x "$ram_boot" ]] || {
    printf 'error: RAM boot runner is not executable: %s\n' "$ram_boot" >&2
    exit 1
}
[[ -n "$host_dtb" && -f "$host_dtb" ]] || {
    printf 'error: native Zephyr DTB not found: %s\n' "$host_dtb" >&2
    printf 'error: provide NATIVE_ZEPHYR_DTB, ATK_HOST_DTB, or TASK123_BOARD_DTB\n' >&2
    exit 1
}
[[ "$fdt_load" =~ ^0x[0-9a-fA-F]+$ ]] || {
    printf 'error: NATIVE_ZEPHYR_FDT_LOAD must be hexadecimal\n' >&2
    exit 2
}
host_dtb="$(realpath "$host_dtb")"

input_elf="$input_dir/zephyr-periodic.elf"
input_bin="$input_dir/zephyr-periodic.bin"
input_manifest="$input_dir/zephyr-periodic.manifest"
input_memory_overlay="$input_dir/zephyr-periodic-memory.overlay"
input_board_conf="$input_dir/zephyr-periodic-extra.conf"
for path in "$input_elf" "$input_bin" "$input_manifest" "$input_memory_overlay" \
    "$input_board_conf"; do
    [[ -f "$path" ]] || {
        printf 'error: missing native Zephyr board input: %s\n' "$path" >&2
        exit 1
    }
done

manifest_value() {
    local name="$1"
    sed -n "s/^${name}=//p" "$input_manifest"
}

linked_base="$(manifest_value linked_base)"
entry_point="$(manifest_value entry_point)"
manifest_samples="$(manifest_value sample_count)"
board="$(manifest_value board)"
start_gated="$(manifest_value start_gated)"
expected_sha="$(manifest_value sha256)"
actual_sha="$(sha256sum "$input_bin" | awk '{print $1}')"
[[ "$linked_base" =~ ^0x[0-9a-fA-F]+$ ]] || {
    printf 'error: invalid linked_base in manifest: %s\n' "$linked_base" >&2
    exit 1
}
[[ "$entry_point" =~ ^0x[0-9a-fA-F]+$ ]] || {
    printf 'error: invalid entry_point in manifest: %s\n' "$entry_point" >&2
    exit 1
}
(( linked_base == 0x10000000 )) || {
    printf 'error: native board Zephyr image is linked at %s, expected 0x10000000\n' \
        "$linked_base" >&2
    exit 1
}
[[ "$board" == "orangepi_5_ultra_rk3588" ]] || {
    printf 'error: native board manifest has unexpected board: %s\n' "$board" >&2
    exit 1
}
[[ "$manifest_samples" == "$samples" ]] || {
    printf 'error: requested %s samples but manifest contains %s\n' \
        "$samples" "$manifest_samples" >&2
    exit 1
}
[[ "$start_gated" == "0" ]] || {
    printf 'error: native board image must be built with ZEPHYR_START_GATED=0\n' >&2
    exit 1
}
[[ "$actual_sha" == "$expected_sha" ]] || {
    printf 'error: native board image does not match its manifest\n' >&2
    exit 1
}

if [[ -e "$out_dir" && -n "$(find "$out_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    printf 'error: native board output directory is not empty: %s\n' "$out_dir" >&2
    exit 1
fi
mkdir -p "$out_dir"
cp "$input_elf" "$out_dir/zephyr-periodic.elf"
cp "$input_bin" "$out_dir/zephyr-periodic.bin"
cp "$input_manifest" "$out_dir/zephyr-periodic.manifest"
cp "$input_memory_overlay" "$out_dir/zephyr-periodic-memory.overlay"
cp "$input_board_conf" "$out_dir/zephyr-periodic-extra.conf"
cp "$host_dtb" "$out_dir/atk-dlrk3588.dtb"

its="$out_dir/zephyr-periodic.its"
fit="$out_dir/zephyr-periodic.fit"
{
    printf '/dts-v1/;\n\n/ {\n'
    printf '    description = "Native Zephyr periodic %s on ATK-DLRK3588";\n' "$samples"
    printf '    #address-cells = <1>;\n\n    images {\n'
    printf '        kernel-1 {\n'
    printf '            description = "Native Zephyr AArch64 Image";\n'
    printf '            data = /incbin/("%s");\n' "$out_dir/zephyr-periodic.bin"
    printf '            type = "kernel";\n            arch = "arm64";\n'
    printf '            os = "linux";\n            compression = "none";\n'
    printf '            load = <%s>;\n            entry = <%s>;\n' "$linked_base" "$linked_base"
    printf '            hash-1 { algo = "sha256"; };\n        };\n\n'
    printf '        fdt-1 {\n            description = "ATK-DLRK3588 device tree";\n'
    printf '            data = /incbin/("%s");\n' "$out_dir/atk-dlrk3588.dtb"
    printf '            type = "flat_dt";\n            arch = "arm64";\n'
    printf '            compression = "none";\n            load = <%s>;\n' "$fdt_load"
    printf '            hash-1 { algo = "sha256"; };\n        };\n    };\n\n'
    printf '    configurations {\n        default = "conf-1";\n        conf-1 {\n'
    printf '            kernel = "kernel-1";\n            fdt = "fdt-1";\n'
    printf '        };\n    };\n};\n'
} > "$its"
mkimage -f "$its" "$fit"
mkimage -l "$fit" > "$out_dir/fit-info.txt"

raw_log="$out_dir/console.log"
start_ns="$(date +%s%N)"
ATK_BOOT_FORMAT=fit \
ATK_LOG="$raw_log" \
ATK_READY_REGEX="PERIODIC LATENCY COMPLETE samples=$samples" \
ATK_READY_TIMEOUT="$timeout_sec" \
ATK_POST_BOOT_BAUD="$zephyr_baud" \
ATK_POST_BOOT_CAPTURE=0 \
ATK_BREAK_WINDOW="${ATK_BREAK_WINDOW:-300}" \
    "$ram_boot" "$fit"
end_ns="$(date +%s%N)"

python3 - "$raw_log" "$out_dir/zephyr.csv" "$samples" <<'PY'
import csv
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
csv_path = Path(sys.argv[2])
expected_samples = int(sys.argv[3])
text = log_path.read_text(errors="replace")
for fatal in (r"\bESR_EL2\b", r"\bpanic(?:ked)?\b", r"fatal IRQ"):
    if re.search(fatal, text, re.IGNORECASE):
        raise SystemExit(f"native board log contains fatal marker: {fatal}")

header = "sequence,timestamp_ns,deadline_ns,actual_ns,jitter_ns"
start = text.rfind(header)
complete_marker = f"PERIODIC LATENCY COMPLETE samples={expected_samples}"
complete = text.find(complete_marker, start)
if start < 0 or complete < start:
    raise SystemExit("native board Zephyr CSV markers are missing")

row_pattern = re.compile(r"(\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)$")
rows = []
for line in text[start + len(header):complete].splitlines():
    match = row_pattern.fullmatch(line.strip())
    if match:
        rows.append(tuple(map(int, match.groups())))
if len(rows) != expected_samples:
    raise SystemExit(
        f"expected {expected_samples} native board Zephyr samples, found {len(rows)}"
    )
if [row[0] for row in rows] != list(range(expected_samples)):
    raise SystemExit("native board Zephyr sample sequence is incomplete or out of order")
timestamps = [row[1] for row in rows]
if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
    raise SystemExit("native board Zephyr timestamps are not strictly increasing")
if any(abs((row[3] - row[2]) - row[4]) > 1 for row in rows):
    raise SystemExit("native board Zephyr deadline, actual, and jitter fields disagree")

with csv_path.open("w", newline="") as stream:
    writer = csv.writer(stream)
    writer.writerow(header.split(","))
    writer.writerows(rows)
PY

python3 "$repo_root/scripts/test/rt_latency_stats.py" \
    "$out_dir/zephyr.csv" > "$out_dir/stats.txt"
{
    printf 'environment=native-atk-dlrk3588-without-axvisor\n'
    printf 'board=%s\n' "$board"
    printf 'samples=%s\n' "$samples"
    printf 'period_ms=10\n'
    printf 'linked_base=%s\n' "$linked_base"
    printf 'entry_point=%s\n' "$entry_point"
    printf 'uboot_serial_baud=%s\n' "${ATK_BAUD:-1500000}"
    printf 'zephyr_serial_baud=%s\n' "$zephyr_baud"
    printf 'boot_transport=fastboot-stage-ram-only\n'
    printf 'boot_format=fit-aarch64-image-with-dtb\n'
    printf 'host_dtb_sha256=%s\n' "$(sha256sum "$out_dir/atk-dlrk3588.dtb" | awk '{print $1}')"
    printf 'start_ns=%s\n' "$start_ns"
    printf 'end_ns=%s\n' "$end_ns"
    printf 'elapsed_ms=%s\n' "$(( (end_ns - start_ns) / 1000000 ))"
    printf 'source_commit=%s\n' "$(git -C "$repo_root" rev-parse HEAD)"
} > "$out_dir/meta.txt"

(
    cd "$out_dir"
    sha256sum console.log zephyr.csv stats.txt meta.txt fit-info.txt \
        zephyr-periodic.elf zephyr-periodic.bin zephyr-periodic.manifest \
        zephyr-periodic-memory.overlay zephyr-periodic-extra.conf \
        atk-dlrk3588.dtb zephyr-periodic.its \
        zephyr-periodic.fit > sha256sums
)
printf 'accepted native physical-board Zephyr evidence: %s\n' "$out_dir"
