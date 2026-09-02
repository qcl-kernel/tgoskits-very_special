#!/usr/bin/env bash
set -euo pipefail

# Run the communication-share, multi-vCPU Task 1 topology on QEMU. The ncnn
# CPU inference workload substitutes for the RK3588 NPU path.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
output_root="${1:?usage: run-starry-task1-multivcpu.sh OUTPUT_ROOT}"
arms="${STARRY_TASK1_TOPOLOGY_ARMS:-rr,fp-rr}"
repeats="${STARRY_TASK1_TOPOLOGY_REPEATS:-1}"
rr_host="$repo_root/scripts/test/net-dual-guest/axvisor-qemu-starry-task1-rr.toml"
fp_host="$repo_root/scripts/test/net-dual-guest/axvisor-qemu-starry-task1-fp-rr.toml"
qemu_config="$repo_root/scripts/test/net-dual-guest/qemu-aarch64-starry-zephyr-task1-multivcpu.toml"
starry_vm="$repo_root/scripts/test/net-dual-guest/vm-aarch64-starry-task1-multivcpu.toml"
zephyr_vm_template="$repo_root/scripts/test/net-dual-guest/vm-aarch64-zephyr-task1-multivcpu.toml"
verifier="$repo_root/scripts/test/net-dual-guest/verify_starry_task1_multivcpu.py"
zephyr_dir="${STARRY_TASK1_TOPOLOGY_ZEPHYR_DIR:-$repo_root/tmp/net-dual-guest/zephyr-task2-starry-normal}"
zephyr_bin="$zephyr_dir/zephyr-task2.bin"
zephyr_manifest="$zephyr_dir/manifest.toml"
rootfs="${STARRY_TASK23_ROOTFS:-$repo_root/tmp/axbuild/rootfs/rootfs-aarch64-alpine.img}"
starry_image="$repo_root/target/aarch64-unknown-none-softfloat/release/starryos.bin"
endpoint="$repo_root/target/starryos-task2-rust/aarch64-unknown-linux-musl/release/starryos-task2-endpoint"
endpoint_script="$repo_root/apps/starry/starryos-task2/t2n1-run.sh"
affinity_helper="$repo_root/target/starryos-task2-rust/task123-affinity"
wait_log_script="$repo_root/apps/starry/starryos-task2/wait-log.sh"
topology_script="$repo_root/apps/starry/starryos-task2/task1-topology.sh"
host_prompt_pattern='axvisor:/\$'
socket_dir=""
rootfs_dir=""
qemu_sock=""
serial_sock=""
run_pid=""

[[ "$repeats" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_TOPOLOGY_REPEATS must be positive\n' >&2
    exit 2
}
case "$arms" in
    rr|fp-rr|rr,fp-rr) ;;
    *)
        printf 'error: STARRY_TASK1_TOPOLOGY_ARMS must be rr, fp-rr, or rr,fp-rr\n' >&2
        exit 2
        ;;
esac
[[ ! -e "$output_root" ]] || {
    printf 'error: output already exists: %s\n' "$output_root" >&2
    exit 2
}
if [[ "${ALLOW_DIRTY:-0}" != 1 ]] &&
    [[ -n "$(git -C "$repo_root" status --porcelain --untracked-files=no)" ]]; then
    printf 'error: tracked worktree changes exist; commit them or set ALLOW_DIRTY=1\n' >&2
    exit 1
fi
for artifact in "$zephyr_bin" "$zephyr_manifest" "$rootfs" "$starry_image" \
    "$endpoint" "$endpoint_script" "$affinity_helper" "$wait_log_script" \
    "$topology_script"; do
    [[ -s "$artifact" ]] || {
        printf 'error: missing experiment artifact: %s; run task123.sh build task1-multivcpu first\n' \
            "$artifact" >&2
        exit 1
    }
done

read -r sample_count dump_chunk_rows fault_mode < <(
    python3 - "$zephyr_manifest" <<'PY'
import sys
import tomllib
from pathlib import Path

with Path(sys.argv[1]).open("rb") as stream:
    manifest = tomllib.load(stream)
print(
    manifest.get("periodic_sample_count", ""),
    manifest.get("periodic_dump_chunk_rows", ""),
    manifest.get("fault_mode", ""),
)
PY
)
[[ "$sample_count" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: invalid periodic sample count in %s\n' "$zephyr_manifest" >&2
    exit 1
}
[[ "$dump_chunk_rows" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: invalid periodic dump chunk size in %s\n' "$zephyr_manifest" >&2
    exit 1
}
[[ "$fault_mode" == none ]] || {
    printf 'error: multi-vCPU Task 1 requires a fault-free Zephyr image\n' >&2
    exit 1
}

python3 "$verifier" --repo "$repo_root"

guest_duration_sec=$(((sample_count * 10 + 999) / 1000))
measurement_timeout_sec="${STARRY_TASK1_MEASUREMENT_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 600))}"
qemu_timeout_sec="${STARRY_TASK1_QEMU_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 900))}"
[[ "$measurement_timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_MEASUREMENT_TIMEOUT_SEC must be positive\n' >&2
    exit 2
}
[[ "$qemu_timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_QEMU_TIMEOUT_SEC must be positive\n' >&2
    exit 2
}

acquire_task123_qemu_slot "$repo_root"
socket_dir="$(create_task123_runtime_dir)"
rootfs_parent="${TASK123_ROOTFS_RUNTIME_PARENT:-$repo_root/tmp/task123-runtime}"
rootfs_dir="$(create_task123_runtime_dir "$rootfs_parent")"
qemu_sock="$socket_dir/qmp.sock"
serial_sock="$socket_dir/serial.sock"

stop_owned_run() {
    if [[ -n "$run_pid" && -S "$qemu_sock" ]]; then
        python3 "$repo_root/scripts/test/net-dual-guest/qmp_link.py" "$qemu_sock" quit \
            >/dev/null 2>&1 || true
    fi
    if [[ -n "$run_pid" ]] && kill -0 "$run_pid" 2>/dev/null; then
        kill -TERM "$run_pid" 2>/dev/null || true
        wait "$run_pid" 2>/dev/null || true
    fi
    [[ -z "$socket_dir" || ! -d "$socket_dir" ]] || remove_task123_runtime_dir "$socket_dir"
    [[ -z "$rootfs_dir" || ! -d "$rootfs_dir" ]] || remove_task123_runtime_dir "$rootfs_dir"
}
trap stop_owned_run EXIT

mkdir -p "$output_root"
zephyr_vm="$output_root/vm-zephyr.runtime.toml"
python3 "$repo_root/scripts/test/net-dual-guest/render_vm_entry.py" \
    "$zephyr_manifest" "$zephyr_vm_template" "$zephyr_vm" \
    --kernel-path "$zephyr_bin"
cp "$zephyr_manifest" "$output_root/zephyr-manifest.toml"
cat > "$output_root/protocol.txt" <<EOF
experiment=task1-qemu-multivcpu
equivalence=cpu-role-placement-and-scheduler-contention
non_equivalence=rk3588-npu,irq-dma,soc-bandwidth,absolute-latency
qemu_pcpus=3
starry_vcpu0=pcpu2,guest-cpu0,ncnn-yolo
starry_vcpu1=pcpu1,guest-cpu1,t2n1
zephyr_vcpu0=pcpu1,priority90,period10ms
starry_priority=89
arms=$arms
repeats=$repeats
samples=$sample_count
EOF

write_equivalence_hashes() {
    local destination="$1" name path digest
    shift
    : > "$destination"
    while (($#)); do
        name="$1"
        path="$2"
        shift 2
        digest="$(sha256sum "$path" | awk '{print $1}')"
        printf '%s=%s\n' "$name" "$digest" >> "$destination"
    done
}

run_arm() {
    local arm="$1" run_number="$2" host_config="$3"
    local run_id run_dir steps diagnostic_steps build_log run_log capture_prefix
    local runtime_rootfs runtime_qemu_config console_status dump_end
    printf -v run_id '%s-%02d' "$arm" "$run_number"
    run_dir="$output_root/$run_id"
    steps="$run_dir/steps.txt"
    diagnostic_steps="$run_dir/stall-diagnostic-steps.txt"
    build_log="$run_dir/build.log"
    run_log="$run_dir/run.log"
    capture_prefix="$run_dir/capture"
    runtime_rootfs="$rootfs_dir/rootfs-$run_id.img"
    runtime_qemu_config="$run_dir/qemu.runtime.toml"
    mkdir -p "$run_dir"
    cp "$host_config" "$run_dir/host-config.toml"
    cp "$qemu_config" "$run_dir/qemu.source.toml"
    cp "$starry_vm" "$run_dir/vm-starry.toml"
    cp "$zephyr_vm" "$run_dir/vm-zephyr.toml"
    cp --reflink=auto --sparse=always "$rootfs" "$runtime_rootfs"
    rm -f -- "$qemu_sock" "$serial_sock"
    python3 "$repo_root/scripts/test/net-dual-guest/render_qemu_runtime.py" \
        "$qemu_config" "$runtime_qemu_config" \
        --rootfs "$runtime_rootfs" \
        --serial-socket "$serial_sock" \
        --qmp-socket "$qemu_sock" \
        --timeout "$qemu_timeout_sec"

    cat > "$steps" <<'EOF'
expect 120 use (Round-robin|Fixed-priority round-robin) scheduler\.
expect 120 \[VM 1\] Use .*apk
send-until 30 1 \x18h axvisor:/\$
cmd vm show 1 --full
expect 30 VCPUs:\s+2
expect 30 VCpu 0: Physical CPU mask 0x4, PCpu ID 2
expect 30 VCpu 1: Physical CPU mask 0x2, PCpu ID 1
cmd vm show 2 --full
expect 30 VCPUs:\s+1
expect 30 VCpu 0: Physical CPU mask 0x2, PCpu ID 1
attach 1
expect 120 root@starry:/root #
cmd test "$(nproc)" -eq 2 && echo TASK1_TOPOLOGY_CPU_ONLINE count=2
expect 20 TASK1_TOPOLOGY_CPU_ONLINE count=2
cmd t1 p0
expect 20 TASK1_TOPOLOGY_PROBE cpu=0 allowed=0
cmd t1 p1
expect 20 TASK1_TOPOLOGY_PROBE cpu=1 allowed=1
cmd t1 c
expect 20 TASK1_TOPOLOGY_COMMUNICATION_STARTED cpu=1
cmd t1 wc
expect 20 TASK2_CONTROLLER_READY mode=task2
cmd t1 a
expect 20 TASK1_TOPOLOGY_AI_STARTED cpu=0
cmd t1 wa
expect 20 TASK3_MODEL_READY model=yolo11n.ncnn runtime=ncnn
expect 20 TASK3_INFER_STARTED model=yolo11n.ncnn request=1 phase=startup
attach 2
expect 120 PERIODIC LATENCY READY
raw g
expect 10 PERIODIC LATENCY START
EOF
    printf 'expect %s PERIODIC LATENCY SAMPLING COMPLETE samples=%s controls=[0-9]+ statuses=[0-9]+ heartbeats=[0-9]+\n' \
        "$measurement_timeout_sec" "$sample_count" >> "$steps"
    cat >> "$steps" <<EOF
send-until 10 1 \\x18h $host_prompt_pattern
cmd virtnet capture on
expect 20 virtnet: capture ON
hold 10
cmd virtnet capture off
expect 20 virtnet: capture OFF
attach 2
EOF
    dump_end=0
    while ((dump_end < sample_count)); do
        dump_end=$((dump_end + dump_chunk_rows))
        ((dump_end <= sample_count)) || dump_end=$sample_count
        cat >> "$steps" <<EOF
raw d
expect 60 PERIODIC LATENCY CHUNK end=$dump_end
EOF
    done
    cat >> "$steps" <<EOF
expect 60 PERIODIC LATENCY COMPLETE samples=$sample_count
attach 1
expect 30 root@starry:/root #
cmd t1 l
expect 20 TASK1_TOPOLOGY_WORKLOADS_ALIVE ai=true communication=true
cmd t1 f
expect 20 TASK1_TOPOLOGY_AFFINITY cpu=0 verified=true
expect 20 TASK1_TOPOLOGY_AFFINITY cpu=1 verified=true
cmd t1 i
expect 30 TASK3_INFER model=yolo11n.ncnn .*request=1
cmd t1 t
expect 30 STARRY_T2N1_CONTROL_SENT
expect 30 STARRY_T2N1_ACK
expect 30 STARRY_T2N1_STATUS_DELIVERED.*request=3
send-until 10 1 \x18h $host_prompt_pattern
cmd vm show 1 --full
expect 30 VCPUs:\s+2
cmd vm show 2 --full
expect 30 VCPUs:\s+1
cmd rt stat
expect 30 RT vCPU wait counters:
expect 30 vcpu=0
expect 30 vcpu=1
dump-pcap $capture_prefix
qmp-quit $qemu_sock
EOF
    cat > "$diagnostic_steps" <<'EOF'
detach-if-attached
cmd rt stat
expect 30 RT vCPU wait counters:
hold 2
EOF

    write_equivalence_hashes "$run_dir/equivalence-hashes.txt" \
        starryos.bin "$starry_image" \
        rootfs.img "$rootfs" \
        zephyr.bin "$zephyr_bin" \
        zephyr.manifest "$zephyr_manifest" \
        vm-starry.toml "$starry_vm" \
        vm-zephyr.toml "$zephyr_vm" \
        qemu.toml "$qemu_config" \
        endpoint "$endpoint" \
        endpoint-script "$endpoint_script" \
        affinity-helper "$affinity_helper" \
        wait-log-script "$wait_log_script" \
        topology-script "$topology_script"
    printf 'command=cargo xtask axvisor qemu --config %s --qemu-config %s --vmconfigs %s --vmconfigs %s --rootfs %s\n' \
        "$host_config" "$runtime_qemu_config" "$starry_vm" "$zephyr_vm" "$runtime_rootfs" \
        > "$run_dir/command.txt"

    printf 'TASK1_TOPOLOGY_RUN_START arm=%s run=%s\n' "$arm" "$run_number"
    (
        cd "$repo_root"
        cargo xtask axvisor qemu \
            --config "$host_config" \
            --qemu-config "$runtime_qemu_config" \
            --vmconfigs "$starry_vm" \
            --vmconfigs "$zephyr_vm" \
            --rootfs "$runtime_rootfs"
    ) > "$build_log" 2>&1 &
    run_pid=$!
    for _ in $(seq 1 300); do
        [[ -S "$serial_sock" ]] && break
        if ! kill -0 "$run_pid" 2>/dev/null; then
            printf 'error: AxVisor exited before serial socket creation\n' >&2
            tail -40 "$build_log" >&2
            return 1
        fi
        sleep 1
    done
    [[ -S "$serial_sock" ]] || {
        printf 'error: serial socket did not appear\n' >&2
        return 1
    }
    set +e
    python3 "$repo_root/scripts/test/net-dual-guest/serial_console.py" \
        "$serial_sock" "$run_log" --script "$steps" --verbose \
        --qmp-sock "$qemu_sock" --forensics-dir "$run_dir/forensics" \
        >/dev/null 2>> "$build_log"
    console_status=$?
    set -e
    if ((console_status != 0)); then
        python3 "$repo_root/scripts/test/net-dual-guest/serial_console.py" \
            "$serial_sock" "$run_log" --script "$diagnostic_steps" --verbose \
            >/dev/null 2>> "$build_log" || true
        return "$console_status"
    fi
    wait "$run_pid" 2>/dev/null || true
    run_pid=""
    rm -f -- "$runtime_rootfs"
    mv "$capture_prefix.vm1.pcap" "$run_dir/starry.pcap"
    mv "$capture_prefix.vm2.pcap" "$run_dir/zephyr.pcap"
    sha256sum "$repo_root/target/aarch64-unknown-linux-musl/release/axvisor.bin" \
        > "$run_dir/axvisor-hash.txt"
    python3 "$verifier" --repo "$repo_root" --run-dir "$run_dir" \
        --arm "$arm" --sample-count "$sample_count" \
        | tee "$run_dir/verify.log"
    printf 'TASK1_TOPOLOGY_RUN_COMPLETE arm=%s run=%s\n' "$arm" "$run_number"
}

for run_number in $(seq 1 "$repeats"); do
    if [[ ",$arms," == *,rr,* ]]; then
        run_arm rr "$run_number" "$rr_host"
    fi
    if [[ ",$arms," == *,fp-rr,* ]]; then
        run_arm fp-rr "$run_number" "$fp_host"
    fi
done

if [[ "$arms" == rr,fp-rr ]]; then
    python3 "$verifier" --repo "$repo_root" --matrix "$output_root" \
        | tee "$output_root/verify-matrix.log"
    rr_runs=()
    fp_rr_runs=()
    for run_number in $(seq 1 "$repeats"); do
        printf -v run_id '%02d' "$run_number"
        rr_runs+=("$output_root/rr-$run_id")
        fp_rr_runs+=("$output_root/fp-rr-$run_id")
    done
    comparison="$output_root/comparison.md"
    python3 "$repo_root/scripts/test/net-dual-guest/analyze_starry_task1_periodic_ab.py" \
        --rr "${rr_runs[@]}" --fp-rr "${fp_rr_runs[@]}" \
        --sample-count "$sample_count" --load-mode yolo \
        --contention-role communication --rtos-name zephyr \
        --output "$comparison"
    printf '\nTask 1 multi-vCPU QEMU result\n\n'
    cat "$comparison"
fi
write_task123_source_identity "$repo_root" "$output_root"
find "$output_root" -type f ! -name SHA256SUMS.txt -print0 \
    | sort -z | xargs -0 sha256sum > "$output_root/SHA256SUMS.txt"
printf 'PASS: Task 1 multi-vCPU evidence retained in %s\n' "$output_root"
