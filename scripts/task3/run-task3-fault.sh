#!/usr/bin/env bash
set -euo pipefail

# Task-3 M5 runtime fault-injection experiment.
#
# QEMU `set_link` only toggles the virtio-net link-status bit and does not
# stop the data path in the virtio-mmio passthrough setup used here (neither
# at runtime nor before guest boot), and `netdev_del` cannot be restored
# cleanly because the NIC keeps the old socket peer.  This script therefore
# routes the two guests through the P3 frame relay (`ack_drop_proxy.py`) and
# lets the proxy discard every frame in both directions for a timed blackout
# window.  The blackout is a real runtime data-link outage between the two
# QEMU netdevs: both T2N1 endpoints exhaust retransmission/heartbeat and
# enter Safe, then recover when the window ends.
#
# Usage: run-task3-fault.sh <label> [baseline|cnn|yolo]
#        [blackout|ack-drop|injection] [out-of-order|invalid-parameter]
# Env:   BLACKOUT_START_MS (default 25000), BLACKOUT_DURATION_MS (default 10000)

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
rootfs="${STARRY_TASK23_ROOTFS:-$repo_root/tmp/axbuild/rootfs/rootfs-aarch64-alpine.img}"
label="${1:?label required}"
mode="${2:-yolo}"
fault="${3:-blackout}"
injection_mode="${4:-out-of-order}"
workdir="$repo_root/tmp/net-dual-guest"
out_dir="${TASK3_OUTPUT_DIR:-$repo_root/results/task3/fault-$label}"
runtime_dir=""
log=""
proxy_log=""
qemu_sock=""
qmp="$repo_root/scripts/test/net-dual-guest/qmp_link.py"
proxy="$repo_root/scripts/test/net-dual-guest/ack_drop_proxy.py"
blackout_start_ms="${BLACKOUT_START_MS:-25000}"
blackout_duration_ms="${BLACKOUT_DURATION_MS:-10000}"
proxy_pid=""
run_pid=""

case "$mode" in
    baseline|cnn|yolo)
        initramfs="$workdir/linux-task2/task2-linux-initramfs-${mode}.cpio.gz"
        ;;
    ai)
        initramfs="$workdir/linux-task2/task2-linux-initramfs-cnn.cpio.gz"
        mode="cnn"
        ;;
    *) echo "mode must be baseline, cnn, or yolo" >&2; exit 2 ;;
esac
case "$fault" in
    blackout|ack-drop) ;;
    injection)
        case "$injection_mode" in
            out-of-order|invalid-parameter) ;;
            *) echo "injection mode must be out-of-order or invalid-parameter" >&2; exit 2 ;;
        esac
        ;;
    *) echo "fault must be blackout, ack-drop, or injection" >&2; exit 2 ;;
esac
if [ ! -s "$initramfs" ]; then
    echo "missing or empty initramfs: $initramfs" >&2
    exit 1
fi

cleanup() {
    if [[ -n "$run_pid" && -n "$qemu_sock" && -S "$qemu_sock" ]]; then
        python3 "$qmp" "$qemu_sock" quit >/dev/null 2>&1 || true
    fi
    if [[ -n "$run_pid" ]] && kill -0 "$run_pid" 2>/dev/null; then
        kill -TERM "$run_pid" 2>/dev/null || true
        wait "$run_pid" 2>/dev/null || true
    fi
    if [ -n "$proxy_pid" ]; then
        kill "$proxy_pid" 2>/dev/null || true
        wait "$proxy_pid" 2>/dev/null || true
    fi
    if [[ -n "$runtime_dir" && -d "$runtime_dir" ]]; then
        remove_task123_runtime_dir "$runtime_dir"
    fi
}
trap cleanup EXIT

if [[ -d "$out_dir" ]] && find "$out_dir" -mindepth 1 -print -quit | grep -q .; then
    printf 'error: output directory is not empty: %s\n' "$out_dir" >&2
    exit 1
fi
mkdir -p "$out_dir"
out_dir="$(realpath "$out_dir")"
log="$out_dir/guest.log"
proxy_log="$out_dir/proxy.log"
runtime_dir="$(create_task123_runtime_dir)"
qemu_sock="$runtime_dir/qmp.sock"
runtime_rootfs="$runtime_dir/rootfs.img"
runtime_qemu_config="$out_dir/qemu.runtime.toml"
runtime_linux_vm="$out_dir/vm-linux.runtime.toml"
capture_prefix="$runtime_dir/capture"
mapfile -t proxy_ports < <(allocate_task123_tcp_ports 2)
linux_proxy_port="${proxy_ports[0]}"
rtos_proxy_port="${proxy_ports[1]}"
cp --reflink=auto --sparse=always "$rootfs" "$runtime_rootfs"
python3 "$repo_root/scripts/test/net-dual-guest/render_qemu_runtime.py" \
    "$repo_root/scripts/test/net-dual-guest/qemu-aarch64-p3-proxy-final.toml" \
    "$runtime_qemu_config" --rootfs "$runtime_rootfs" --qmp-socket "$qemu_sock" \
    --capture-prefix "$capture_prefix" --netdev-port "12731=$linux_proxy_port" \
    --netdev-port "12732=$rtos_proxy_port"
python3 "$repo_root/scripts/test/net-dual-guest/render_vm_runtime.py" \
    "$repo_root/scripts/test/net-dual-guest/vm-aarch64-p2-linux.toml" \
    "$runtime_linux_vm" --ramdisk-path "$initramfs"

if [ "$fault" = blackout ]; then
    drop_count=0
    blackout_args=(--blackout-start-ms "$blackout_start_ms" --blackout-duration-ms "$blackout_duration_ms")
    inject_args=()
elif [ "$fault" = injection ]; then
    drop_count=0
    blackout_args=()
    inject_args=(--inject "$injection_mode")
else
    drop_count=1
    blackout_args=()
    inject_args=()
fi

# Start the relay first: both QEMU netdevs connect to it.
nohup python3 "$proxy" \
    --linux-port "$linux_proxy_port" --rtos-port "$rtos_proxy_port" \
    --drop-direction rtos-to-linux --drop-kind ack --drop-count "$drop_count" \
    "${blackout_args[@]}" \
    "${inject_args[@]}" \
    --log "$proxy_log" &
proxy_pid=$!
for _ in $(seq 1 30); do
    if grep -q "PROXY_READY" "$proxy_log" 2>/dev/null; then
        break
    fi
    sleep 1
done
if ! grep -q "PROXY_READY" "$proxy_log" 2>/dev/null; then
    echo "proxy failed to start; tail:"; tail -5 "$proxy_log"; exit 1
fi

nohup cargo xtask axvisor qemu \
    --config scripts/test/net-dual-guest/axvisor-qemu-debug.toml \
    --qemu-config "$runtime_qemu_config" \
    --vmconfigs "$runtime_linux_vm" \
    --vmconfigs scripts/test/net-dual-guest/vm-aarch64-p2-rtos.toml \
    --rootfs "$runtime_rootfs" \
    > "$log" 2>&1 &
run_pid=$!

# Only match log lines that appear after the previous wait completed; the
# guest log is append-only and patterns like TASK2_RECOVERED also occur in
# earlier boot sequences, so scanning from a moving offset keeps the evidence
# ordering honest.
offset=0
wait_for_new() {
    local pattern="$1" timeout="${2:-180}"
    local deadline=$(( $(date +%s) + timeout ))
    while (( $(date +%s) < deadline )); do
        local lines
        lines=$(wc -l < "$log")
        if (( lines > offset )); then
            local relative
            relative=$(tail -n +$((offset + 1)) "$log" | grep -n -m1 -E "$pattern" | cut -d: -f1 || true)
            if [ -n "$relative" ]; then
                # Advance only to the matched line.  A single read may contain
                # several markers (especially protocol injection), and moving
                # to the end would silently skip the later evidence.
                offset=$((offset + relative - 1))
                return 0
            fi
        fi
        if ! kill -0 "$run_pid" 2>/dev/null; then
            echo "run process exited early; tail:"
            tail -5 "$log"
            exit 1
        fi
        sleep 3
    done
    echo "timeout waiting for $pattern"
    exit 1
}

echo "[fault] waiting for the control loop to start"
wait_for_new "TASK3_CONTROL_SENT" 300

if [ "$fault" = blackout ]; then
    echo "[fault] control loop running; blackout starts at ${blackout_start_ms}ms after connect"
    echo "[fault] waiting for safe state caused by the blackout"
    wait_for_new "TASK2_SAFE" 120

    echo "[fault] safe state observed; waiting for recovery after blackout ends"
    wait_for_new "TASK2_RECOVERED" 120
    echo "[fault] waiting for a resumed control loop"
    wait_for_new "TASK3_CONTROL_SENT elapsed_ms=" 120
    sleep 10
elif [ "$fault" = injection ]; then
    echo "[fault] waiting for injected ${injection_mode} protocol rejection"
    if [ "$injection_mode" = out-of-order ]; then
        wait_for_new "TASK2_PROTOCOL_ERROR out_of_order=99" 120
        wait_for_new "TASK2_REMOTE_ERROR code=OutOfOrder" 120
    else
        wait_for_new "TASK2_PROTOCOL_ERROR (invalid_payload=|invalid_parameter seq=)" 120
        wait_for_new "TASK2_REMOTE_ERROR code=InvalidParameter" 120
    fi
    sleep 8
else
    echo "[fault] waiting for ACK retransmission and duplicate handling"
    wait_for_new "TASK2_RETRANSMIT" 120
    wait_for_new "TASK2_DUPLICATE" 120
    sleep 8
fi

echo "[fault] quitting"
python3 "$qmp" "$qemu_sock" quit || true
sleep 15
kill "$proxy_pid" 2>/dev/null || true
wait "$proxy_pid" 2>/dev/null || true
proxy_pid=""
wait "$run_pid" 2>/dev/null || true
run_pid=""
echo "run $label mode=$mode fault=$fault finished; log=$log proxy_log=$proxy_log"

for pcap in "$capture_prefix.vm1.pcap" "$capture_prefix.vm2.pcap"; do
    if [ ! -s "$pcap" ]; then
        echo "missing or empty pcap: $pcap" >&2
        exit 1
    fi
done

mv "$capture_prefix.vm1.pcap" "$out_dir/linux.pcap"
mv "$capture_prefix.vm2.pcap" "$out_dir/rtos.pcap"

if [ "$fault" = ack-drop ]; then
    drop_ack="$(sed -n 's/.*PROXY_DROP .*ack=\([0-9][0-9]*\).*/\1/p' "$proxy_log" | head -1)"
    if [ -z "$drop_ack" ]; then
        echo "proxy did not record a dropped ACK" >&2
        exit 1
    fi
    python3 "$repo_root/scripts/test/net-dual-guest/verify_fault_pcap.py" \
        "$out_dir/linux.pcap" "$out_dir/rtos.pcap" \
        --drop-src 10.0.42.2 --drop-dst 10.0.42.15 \
        --drop-kind ack --drop-ack "$drop_ack" --drop-count 1 \
        --guest-log "$out_dir/guest.log" --proxy-log "$out_dir/proxy.log"
elif [ "$fault" = injection ]; then
    python3 "$repo_root/scripts/test/net-dual-guest/verify_protocol_injection.py" \
        "$out_dir/rtos.pcap" --mode "$injection_mode" \
        --guest-log "$out_dir/guest.log" --proxy-log "$out_dir/proxy.log"
else
    python3 "$repo_root/scripts/test/net-dual-guest/verify_pcap.py" \
        --tag '' --require-task2 "$out_dir/linux.pcap" "$out_dir/rtos.pcap"
fi

{
    printf 'label = "%s"\n' "$label"
    printf 'mode = "%s"\n' "$mode"
    printf 'fault = "%s"\n' "$fault"
    printf 'git_head = "%s"\n' "$(git -C "$repo_root" rev-parse HEAD)"
    printf 'guest_log_sha256 = "%s"\n' "$(sha256sum "$out_dir/guest.log" | awk '{print $1}')"
    printf 'proxy_log_sha256 = "%s"\n' "$(sha256sum "$out_dir/proxy.log" | awk '{print $1}')"
    printf 'linux_pcap_sha256 = "%s"\n' "$(sha256sum "$out_dir/linux.pcap" | awk '{print $1}')"
    printf 'rtos_pcap_sha256 = "%s"\n' "$(sha256sum "$out_dir/rtos.pcap" | awk '{print $1}')"
    if [ "$fault" = ack-drop ]; then
        printf 'dropped_ack = %s\n' "$drop_ack"
    elif [ "$fault" = injection ]; then
        printf 'injection_mode = "%s"\n' "$injection_mode"
    else
        printf 'blackout_start_ms = %s\n' "$blackout_start_ms"
        printf 'blackout_duration_ms = %s\n' "$blackout_duration_ms"
    fi
} > "$out_dir/manifest.toml"
write_task123_source_identity "$repo_root" "$out_dir"
echo "archived evidence under $out_dir"
