#!/usr/bin/env bash
set -euo pipefail

# Run one legacy Linux/RTOS Task-3 experiment with per-run host resources.
# New submission evidence should use scripts/competition/task123.sh.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
rootfs="$(resolve_task123_rootfs "$repo_root")"
label="${1:?label required}"
mode="${2:?mode required: ai or baseline}"
min_elapsed_ms="${MIN_ELAPSED_MS:-35000}"
workdir="$repo_root/tmp/net-dual-guest"
output_dir="${TASK3_OUTPUT_DIR:-$repo_root/results/task3/legacy/$label}"
runtime_dir=""
run_pid=""

case "$mode" in
    ai) initramfs="$workdir/linux-task2/task2-linux-initramfs-ai.cpio.gz" ;;
    baseline) initramfs="$workdir/linux-task2/task2-linux-initramfs-baseline.cpio.gz" ;;
    *) printf 'error: mode must be ai or baseline\n' >&2; exit 2 ;;
esac
[[ "$min_elapsed_ms" =~ ^[0-9]+$ ]] || {
    printf 'error: MIN_ELAPSED_MS must be a non-negative integer\n' >&2
    exit 2
}
for artifact in "$rootfs" "$initramfs"; do
    [[ -s "$artifact" ]] || { printf 'error: missing artifact: %s\n' "$artifact" >&2; exit 1; }
done
if [[ -d "$output_dir" ]] && find "$output_dir" -mindepth 1 -print -quit | grep -q .; then
    printf 'error: output directory is not empty: %s\n' "$output_dir" >&2
    exit 1
fi
mkdir -p "$output_dir"
output_dir="$(realpath "$output_dir")"
log="$output_dir/run.log"

runtime_dir="$(create_task123_runtime_dir)"
qemu_sock="$runtime_dir/qmp.sock"
runtime_rootfs="$runtime_dir/rootfs.img"
runtime_qemu_config="$output_dir/qemu.runtime.toml"
runtime_linux_vm="$output_dir/vm-linux.runtime.toml"
capture_prefix="$runtime_dir/capture"
netdev_port="$(allocate_task123_tcp_ports)"
cp --reflink=auto --sparse=always "$rootfs" "$runtime_rootfs"
python3 "$repo_root/scripts/test/net-dual-guest/render_qemu_runtime.py" \
    "$repo_root/scripts/test/net-dual-guest/qemu-aarch64-p2.toml" \
    "$runtime_qemu_config" --rootfs "$runtime_rootfs" --qmp-socket "$qemu_sock" \
    --capture-prefix "$capture_prefix" --netdev-port "12721=$netdev_port"
python3 "$repo_root/scripts/test/net-dual-guest/render_vm_runtime.py" \
    "$repo_root/scripts/test/net-dual-guest/vm-aarch64-p2-linux.toml" \
    "$runtime_linux_vm" --ramdisk-path "$initramfs"

cleanup() {
    if [[ -n "$run_pid" && -S "$qemu_sock" ]]; then
        python3 "$repo_root/scripts/test/net-dual-guest/qmp_link.py" "$qemu_sock" quit \
            >/dev/null 2>&1 || true
    fi
    if [[ -n "$run_pid" ]] && kill -0 "$run_pid" 2>/dev/null; then
        kill -TERM "$run_pid" 2>/dev/null || true
        wait "$run_pid" 2>/dev/null || true
    fi
    if [[ -n "$runtime_dir" && -d "$runtime_dir" ]]; then
        remove_task123_runtime_dir "$runtime_dir"
    fi
}
trap cleanup EXIT

(
    cd "$repo_root"
    cargo xtask axvisor qemu \
        --config scripts/test/net-dual-guest/axvisor-qemu-debug.toml \
        --qemu-config "$runtime_qemu_config" \
        --vmconfigs "$runtime_linux_vm" \
        --vmconfigs scripts/test/net-dual-guest/vm-aarch64-p2-rtos.toml \
        --rootfs "$runtime_rootfs"
) > "$log" 2>&1 &
run_pid=$!

deadline=$(( $(date +%s) + 900 ))
while (( $(date +%s) < deadline )); do
    if ! kill -0 "$run_pid" 2>/dev/null; then
        printf 'error: run process exited early\n' >&2
        tail -20 "$log" >&2
        exit 1
    fi
    if [[ -S "$qemu_sock" ]]; then
        last_elapsed="$(grep -oE 'TASK3_(STATUS_RECEIVED|CONTROL_SENT) elapsed_ms=[0-9]+' "$log" \
            | grep -oE '[0-9]+$' | tail -1 || true)"
        if [[ -n "$last_elapsed" && "$last_elapsed" -ge "$min_elapsed_ms" ]]; then
            python3 "$repo_root/scripts/test/net-dual-guest/qmp_link.py" "$qemu_sock" quit || true
            break
        fi
    fi
    sleep 15
done
wait "$run_pid" 2>/dev/null || true
run_pid=""
for index in 1 2; do
    capture="$capture_prefix.vm$index.pcap"
    [[ -s "$capture" ]] && mv "$capture" "$output_dir/guest$index.pcap"
done
write_task123_source_identity "$repo_root" "$output_dir"
printf 'run %s (%s) finished; evidence=%s\n' "$label" "$mode" "$output_dir"
