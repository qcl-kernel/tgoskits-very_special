#!/usr/bin/env bash
set -euo pipefail

# Measure Zephyr periodic wake-up latency while StarryOS runs real ncnn/YOLO.
# The two arms differ only in the AxVisor scheduler feature.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
output_root="${1:?usage: run-starry-task1-periodic-ab.sh OUTPUT_ROOT}"
repeats="${STARRY_TASK1_PERIODIC_REPEATS:-1}"
arms="${STARRY_TASK1_PERIODIC_ARMS:-rr,fp-rr}"
load_mode="${STARRY_TASK1_LOAD_MODE:-yolo}"
rr_host="$repo_root/scripts/test/net-dual-guest/axvisor-qemu-starry-task1-rr.toml"
fp_host="$repo_root/scripts/test/net-dual-guest/axvisor-qemu-starry-task1-fp-rr.toml"
qemu_config="$repo_root/scripts/test/net-dual-guest/qemu-aarch64-starry-zephyr-task1-capture.toml"
starry_vm="$repo_root/scripts/test/net-dual-guest/vm-aarch64-starry-task1-shared.toml"
zephyr_vm_template="$repo_root/scripts/test/net-dual-guest/vm-aarch64-zephyr-periodic-task1-shared.toml"
analyzer="$repo_root/scripts/test/net-dual-guest/analyze_starry_task1_periodic_ab.py"
socket_dir=""
qemu_sock=""
serial_sock=""
periodic_dir="${STARRY_TASK1_PERIODIC_DIR:-$repo_root/tmp/starry-task1-periodic}"
periodic_bin="$periodic_dir/zephyr-periodic.bin"
periodic_manifest="$periodic_dir/zephyr-periodic.manifest"
rootfs="${STARRY_TASK23_ROOTFS:-$repo_root/tmp/axbuild/rootfs/rootfs-aarch64-alpine.img}"
run_pid=""

[[ "$repeats" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_PERIODIC_REPEATS must be positive\n' >&2
    exit 2
}
case "$arms" in
    rr|fp-rr|rr,fp-rr) ;;
    *)
        printf 'error: STARRY_TASK1_PERIODIC_ARMS must be rr, fp-rr, or rr,fp-rr\n' >&2
        exit 2
        ;;
esac
case "$load_mode" in
    idle|yolo) ;;
    *)
        printf 'error: STARRY_TASK1_LOAD_MODE must be idle or yolo\n' >&2
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
for artifact in "$periodic_bin" "$periodic_manifest" "$rootfs" \
    "$repo_root/target/aarch64-unknown-none-softfloat/release/starryos.bin"; do
    [[ -s "$artifact" ]] || {
        printf 'error: missing experiment artifact: %s\n' "$artifact" >&2
        exit 1
    }
done
grep -qx 'start_gated=1' "$periodic_manifest"
sample_count="$(awk -F= '$1 == "sample_count" { print $2 }' "$periodic_manifest")"
[[ "$sample_count" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: periodic manifest has no valid sample_count: %s\n' "$periodic_manifest" >&2
    exit 1
}
dump_gated="$(awk -F= '$1 == "dump_gated" { print $2 }' "$periodic_manifest")"
dump_chunk_rows="$(awk -F= '$1 == "dump_chunk_rows" { print $2 }' "$periodic_manifest")"
[[ "$dump_gated" == 1 ]] || {
    printf 'error: Task 1 evidence probe must use gated CSV export\n' >&2
    exit 1
}
[[ "$dump_chunk_rows" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: periodic manifest has no valid dump_chunk_rows: %s\n' \
        "$periodic_manifest" >&2
    exit 1
}
guest_duration_sec=$(((sample_count * 10 + 999) / 1000))
measurement_timeout_sec="${STARRY_TASK1_MEASUREMENT_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 600))}"
[[ "$measurement_timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_MEASUREMENT_TIMEOUT_SEC must be positive\n' >&2
    exit 2
}
qemu_timeout_sec="${STARRY_TASK1_QEMU_TIMEOUT_SEC:-$((guest_duration_sec * 4 + 900))}"
[[ "$qemu_timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_QEMU_TIMEOUT_SEC must be positive\n' >&2
    exit 2
}
progress_timeout_sec="${STARRY_TASK1_PROGRESS_TIMEOUT_SEC:-30}"
[[ "$progress_timeout_sec" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: STARRY_TASK1_PROGRESS_TIMEOUT_SEC must be positive\n' >&2
    exit 2
}
minimum_inferences=1
if [[ "$load_mode" == yolo && "$sample_count" -ge 6000 ]]; then
    minimum_inferences=$((sample_count * 10 / 1000 / 60 + 1))
fi

python3 - "$rr_host" "$fp_host" <<'PY'
import sys
import tomllib
from pathlib import Path

def load(path):
    with Path(path).open("rb") as stream:
        return tomllib.load(stream)

rr = load(sys.argv[1])
fp = load(sys.argv[2])
rr_features = set(rr.pop("features", []))
fp_features = set(fp.pop("features", []))
if rr != fp:
    raise SystemExit("scheduler A/B configs differ outside features")
if rr_features ^ fp_features != {"rr-scheduler", "fp-rr-scheduler"}:
    raise SystemExit("scheduler A/B feature difference is not RR versus FP-RR")
PY

acquire_task123_qemu_slot "$repo_root"
socket_dir="$(create_task123_runtime_dir)"
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
    if [[ -n "$socket_dir" && -d "$socket_dir" ]]; then
        remove_task123_runtime_dir "$socket_dir"
    fi
}
trap stop_owned_run EXIT

mkdir -p "$output_root"
zephyr_vm="$output_root/vm-zephyr.runtime.toml"
python3 "$repo_root/scripts/test/net-dual-guest/render_vm_entry.py" \
    "$periodic_manifest" "$zephyr_vm_template" "$zephyr_vm" \
    --kernel-path "$periodic_bin"
git -C "$repo_root" rev-parse HEAD > "$output_root/git-head.txt"
cp "$periodic_manifest" "$output_root/zephyr-periodic.manifest"
cat > "$output_root/protocol.txt" <<EOF
experiment=starryos-zephyr-periodic-scheduler
single_variable=axvisor-scheduler-feature-when-both-arms-run
arms=$arms
repeats_per_arm=$repeats
run_order=$arms,repeated
topology=starry-vcpu0->pcpu1,zephyr-vcpu0->pcpu1
starry_host_priority=89
zephyr_host_priority=90
zephyr_period_ms=10
zephyr_samples=$sample_count
evidence_dump=host-acknowledged-chunks
dump_chunk_rows=$dump_chunk_rows
load_mode=$load_mode
interference=$([[ "$load_mode" == yolo ]] && printf 'starry-in-guest-continuous-ncnn-yolo-inference' || printf 'none')
qemu_timeout_sec=$qemu_timeout_sec
minimum_completed_inferences=$minimum_inferences
EOF

run_arm() {
    local arm="$1" run_number="$2" host_config="$3"
    local run_id run_dir steps diagnostic_steps build_log run_log runtime_rootfs runtime_qemu_config
    local console_status
    printf -v run_id '%s-%02d' "$arm" "$run_number"
    run_dir="$output_root/$run_id"
    steps="$run_dir/steps.txt"
    diagnostic_steps="$run_dir/stall-diagnostic-steps.txt"
    build_log="$run_dir/build.log"
    run_log="$run_dir/run.log"
    mkdir -p "$run_dir"
    cp "$host_config" "$run_dir/host-config.toml"
    cp "$qemu_config" "$run_dir/qemu.source.toml"
    cp "$starry_vm" "$run_dir/vm-starry.toml"
    cp "$zephyr_vm" "$run_dir/vm-zephyr.toml"

    rm -f -- "$qemu_sock" "$serial_sock"
    runtime_rootfs="$socket_dir/rootfs-$run_id.img"
    runtime_qemu_config="$run_dir/qemu.runtime.toml"
    cp --reflink=auto --sparse=always "$rootfs" "$runtime_rootfs"
    python3 "$repo_root/scripts/test/net-dual-guest/render_qemu_runtime.py" \
        "$qemu_config" "$runtime_qemu_config" \
        --rootfs "$runtime_rootfs" \
        --serial-socket "$serial_sock" \
        --qmp-socket "$qemu_sock" \
        --timeout "$qemu_timeout_sec"
    cat > "$steps" <<EOF
expect 120 use (Round-robin|Fixed-priority round-robin) scheduler\\.
expect 120 \\[VM 1\\] Use .*apk
detach
expect 20 Welcome to AxVisor Shell!
attach 1
expect 120 root@starry:/root #
EOF
    if [[ "$load_mode" == yolo ]]; then
        cat >> "$steps" <<'EOF'
cmd while :; do sh /usr/bin/t2n1-run.sh model-only; done &
expect 30 TASK3_MODEL_READY model=yolo11n.ncnn runtime=ncnn
expect 30 TASK3_INFER_STARTED model=yolo11n.ncnn request=1 phase=startup
EOF
    fi
    cat >> "$steps" <<EOF
attach 2
expect 120 PERIODIC LATENCY READY
raw g
expect 10 PERIODIC LATENCY START
expect $measurement_timeout_sec PERIODIC LATENCY SAMPLING COMPLETE samples=$sample_count
EOF
    local dump_end=0
    while ((dump_end < sample_count)); do
        dump_end=$((dump_end + dump_chunk_rows))
        if ((dump_end > sample_count)); then
            dump_end=$sample_count
        fi
        cat >> "$steps" <<EOF
raw d
expect 60 PERIODIC LATENCY CHUNK end=$dump_end
EOF
    done
    cat >> "$steps" <<EOF
expect 60 PERIODIC LATENCY COMPLETE samples=$sample_count
EOF
    if [[ "$load_mode" == yolo ]]; then
        cat >> "$steps" <<'EOF'
attach 1
expect 180 TASK3_INFER model=yolo11n.ncnn .*infer_us=.*request=1
EOF
    fi
    cat >> "$steps" <<EOF
detach
cmd rt stat
expect 30 RT vCPU wait counters:
qmp-quit $qemu_sock
EOF
    cat > "$diagnostic_steps" <<EOF
raw \\x18h
expect 10 \\[Axvisor\\] detached VM\\[2\\] console
cmd rt stat
expect 30 RT vCPU wait counters:
hold 2
EOF
    {
        printf 'arm=%s\nrun=%s\nload_mode=%s\nsample_count=%s\ngit_head=%s\n' \
            "$arm" "$run_number" "$load_mode" "$sample_count" \
            "$(git -C "$repo_root" rev-parse HEAD)"
        printf 'command=cargo xtask axvisor qemu --config %s --qemu-config %s --vmconfigs %s --vmconfigs %s --rootfs %s\n' \
            "$host_config" "$runtime_qemu_config" "$starry_vm" "$zephyr_vm" "$runtime_rootfs"
    } > "$run_dir/command.txt"

    printf 'TASK1_PERIODIC_RUN_START arm=%s run=%s\n' "$arm" "$run_number"
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
    for _ in $(seq 1 180); do
        [[ -S "$serial_sock" ]] && break
        if ! kill -0 "$run_pid" 2>/dev/null; then
            printf 'error: AxVisor exited before serial socket creation\n' >&2
            tail -40 "$build_log" >&2
            exit 1
        fi
        sleep 1
    done
    [[ -S "$serial_sock" ]] || {
        printf 'error: serial socket did not appear\n' >&2
        exit 1
    }
    set +e
    (
        cd "$repo_root"
        python3 scripts/test/net-dual-guest/serial_console.py \
            "$serial_sock" "$run_log" --script "$steps" --verbose \
            --progress-regex '[0-9]+,[0-9]+,[0-9]+,[0-9]+,[0-9]+' \
            --progress-timeout "$progress_timeout_sec" \
            --qmp-sock "$qemu_sock" --forensics-dir "$run_dir/forensics"
    ) >/dev/null 2>> "$build_log"
    console_status=$?
    set -e
    if ((console_status != 0)); then
        printf 'TASK1_PERIODIC_STALL_DIAGNOSTIC arm=%s run=%s status=%s\n' \
            "$arm" "$run_number" "$console_status" | tee -a "$build_log"
        (
            cd "$repo_root"
            python3 scripts/test/net-dual-guest/serial_console.py \
                "$serial_sock" "$run_log" --script "$diagnostic_steps" --verbose
        ) >/dev/null 2>> "$build_log" || true
        return "$console_status"
    fi
    wait "$run_pid" 2>/dev/null || true
    run_pid=""
    rm -f -- "$runtime_rootfs"
    sha256sum "$periodic_bin" "$rootfs" \
        "$repo_root/target/aarch64-unknown-none-softfloat/release/starryos.bin" \
        "$repo_root/target/aarch64-unknown-linux-musl/release/axvisor.bin" \
        > "$run_dir/artifact-hashes.txt"
    printf 'TASK1_PERIODIC_RUN_COMPLETE arm=%s run=%s\n' "$arm" "$run_number"
}

rr_runs=()
fp_runs=()
for run_number in $(seq 1 "$repeats"); do
    if [[ ",$arms," == *,rr,* ]]; then
        run_arm rr "$run_number" "$rr_host"
        rr_runs+=("$output_root/rr-$(printf '%02d' "$run_number")")
    fi
    if [[ ",$arms," == *,fp-rr,* ]]; then
        run_arm fp-rr "$run_number" "$fp_host"
        fp_runs+=("$output_root/fp-rr-$(printf '%02d' "$run_number")")
    fi
done

analyzer_args=(
    --sample-count "$sample_count"
    --load-mode "$load_mode"
    --min-inferences "$minimum_inferences"
    --output "$output_root/comparison.md"
)
if ((${#rr_runs[@]} > 0)); then
    analyzer_args+=(--rr "${rr_runs[@]}")
fi
if ((${#fp_runs[@]} > 0)); then
    analyzer_args+=(--fp-rr "${fp_runs[@]}")
fi
python3 "$analyzer" "${analyzer_args[@]}" | tee "$output_root/verify.log"
find "$output_root" -type f ! -name SHA256SUMS.txt -print0 \
    | sort -z | xargs -0 sha256sum > "$output_root/SHA256SUMS.txt"
printf 'PASS: StarryOS Task 1 periodic A/B retained in %s\n' "$output_root"
