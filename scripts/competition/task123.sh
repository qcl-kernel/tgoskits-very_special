#!/usr/bin/env bash
set -euo pipefail

# Judge-facing entrypoint for the Task 1-3 QEMU acceptance scenarios.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
evidence_root="${TASK123_EVIDENCE_DIR:-$repo_root/tmp/competition-task123/evidence}"
ncnn_revision="946fe3fb14a8dff8c06df763f67be522167b2f00"
zephyr_revision="dccb09599635bdff17633fa7e9dab014b91dce90"

usage() {
    cat <<'EOF'
Usage:
  scripts/competition/task123.sh doctor
  scripts/competition/task123.sh prepare
  scripts/competition/task123.sh --list
  scripts/competition/task123.sh build [quick|full]
  scripts/competition/task123.sh run SCENARIO
  scripts/competition/task123.sh suite [task1|task2|task3|quick|acceptance|full|video]
  scripts/competition/task123.sh board COMMAND

Run `scripts/competition/task123.sh --list` for scenario and suite names.
Generated evidence is written below tmp/competition-task123/evidence by default.
EOF
}

list_scenarios() {
    cat <<'EOF'
Scenarios:
  task3-yolo-smoke         Fresh AArch64 ncnn binary performs real YOLO inference
  task1-scheduler-ab       Same dual-Guest load under RR and FP-RR schedulers
  task2-normal             Model-independent CONTROL -> RTOS STATUS/ACK
  task23-integrated        YOLO -> CONTROL -> RTOS STATUS/ACK, with two pcaps
  task2-drop-ack           One lost ACK, retransmission and duplicate suppression
  task2-retry-exhausted    Bounded retries, Safe state and recovery
  task2-blackout           Link blackout, Safe state and post-blackout recovery
  task2-out-of-order       Reject an out-of-order CONTROL frame and recover
  task2-invalid-parameter  Reject an invalid control value and recover
  task3-model-rejected     Invalid model output enters the defined Safe path

Gates:
  ci-contracts             Task 2/3 Rust and Python contract/regression gate

Suites:
  task1       RR versus FP-RR scheduler A/B
  task2       Normal control loop plus all protocol fault scenarios
  task3       Real YOLO smoke and rejected-output safety
  quick       ci-contracts + task3-yolo-smoke
  acceptance  task1-scheduler-ab + normal + blackout + model-rejected
  full        all ten behavioral scenarios
  video       short evidence order used by the recommended recording script

Physical-board commands (RAM-only; never flash or erase):
  native                  Native Zephyr periodic baseline (default 3 runs)
  task1-communication     RR/FP-RR matrix, communication vCPU shares with RTOS
  task2-throughput        Bounded T2N1 transaction benchmark
  task3-build             Fresh fixed/RKNN controllers, payloads and FITs
  task3-matrix            Fixed-perception/RKNN paired scene matrix
  task3-all               Build, RAM-boot, run and quantify Task 3 matrix
  demo                    Render Task 2/3 HTML, SVG and PNG evidence dashboards
  demo-video              Render dashboards and record indexed MP4 replays

Run parameters are environment variables documented in
scripts/competition/README-task123.md and very_special-成果材料/05-复现入口与配置审计.md.
EOF
}

configure_cross_tools() {
    local discovered_cross_root deps_root
    deps_root="${TASK123_DEPS_DIR:-$repo_root/.deps/task123}"
    if [[ -z "${CROSS_ROOT:-}" ]] &&
        [[ -x "$deps_root/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc" ]]; then
        export CROSS_ROOT="$deps_root/aarch64-linux-musl-cross"
    fi
    discovered_cross_root="$(discover_task123_cross_root)"
    if [[ -z "${CROSS_ROOT:-}" && -n "$discovered_cross_root" ]]; then
        export CROSS_ROOT="$discovered_cross_root"
    fi
    if [[ -n "${CROSS_ROOT:-}" ]]; then
        case ":$PATH:" in
            *":$CROSS_ROOT/bin:"*) ;;
            *) export PATH="$CROSS_ROOT/bin:$PATH" ;;
        esac
        export CROSS_CC="${CROSS_CC:-$CROSS_ROOT/bin/aarch64-linux-musl-gcc}"
        export CROSS_CXX="${CROSS_CXX:-$CROSS_ROOT/bin/aarch64-linux-musl-g++}"
        export CROSS_AR="${CROSS_AR:-$CROSS_ROOT/bin/aarch64-linux-musl-ar}"
        export CROSS_RANLIB="${CROSS_RANLIB:-$CROSS_ROOT/bin/aarch64-linux-musl-ranlib}"
        export CROSS_COMPILE="${CROSS_COMPILE:-$CROSS_ROOT/bin/aarch64-linux-musl-}"
    fi
}

configured_tool() {
    local override_name="$1" command_name="$2" configured
    configured="${!override_name:-}"
    if [[ -n "$configured" ]]; then
        [[ -x "$configured" ]] && printf '%s\n' "$configured"
        return
    fi
    command -v "$command_name" 2>/dev/null || true
}

find_ncnn_source() {
    local candidate
    for candidate in \
        "${NCNN_SOURCE:-}" \
        "$repo_root/tmp/task3-yolo/ncnn-source" \
        "$repo_root/tmp/task3-yolo/ncnn-source-fresh" \
        "$repo_root/tmp/competition-task123/downloads/ncnn"; do
        if [[ -n "$candidate" && -f "$candidate/CMakeLists.txt" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
}

find_pnnx() {
    local candidate
    for candidate in \
        "${PNNX:-}" \
        "$(command -v pnnx 2>/dev/null || true)" \
        "$repo_root/tmp/task3-yolo/pnnx-tool/20260526/pnnx-20260526-linux/pnnx" \
        "$repo_root/tmp/competition-task123/downloads/pnnx-20260526-linux/pnnx"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
}

find_yolo_onnx() {
    local candidate
    for candidate in \
        "${YOLO_ONNX:-}" \
        "$repo_root/tmp/competition-task123/downloads/yolo11n.onnx" \
        "$repo_root/tmp/task3-yolo/yolo11n.onnx"; do
        if [[ -n "$candidate" && -s "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
}

find_zephyr_base() {
    local candidate
    for candidate in \
        "${ZEPHYR_BASE:-}" \
        "$repo_root/.deps/task123/zephyr-$zephyr_revision" \
        "$repo_root/tmp/competition-task123/downloads/zephyr-$zephyr_revision"; do
        if [[ -n "$candidate" && -f "$candidate/CMakeLists.txt" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
}

zephyr_source_revision() {
    task123_source_revision "$1"
}

doctor() {
    configure_cross_tools
    local failures=0 tool
    local commands=(
        git cargo rustup python3 cmake ninja qemu-system-aarch64 qemu-aarch64
        debugfs e2fsck sha256sum realpath dtc flock
    )
    printf 'Task 1-3 environment check\n'
    printf '  repository: %s\n' "$repo_root"
    printf '  commit:     %s\n' "$(git -C "$repo_root" rev-parse HEAD)"
    for tool in "${commands[@]}"; do
        if command -v "$tool" >/dev/null 2>&1; then
            printf '  [OK]      %s\n' "$tool"
        else
            printf '  [MISSING] %s\n' "$tool"
            failures=$((failures + 1))
        fi
    done

    local override command_name resolved
    while read -r override command_name; do
        resolved="$(configured_tool "$override" "$command_name")"
        if [[ -n "$resolved" ]]; then
            printf '  [OK]      %s (%s)\n' "$command_name" "$resolved"
        else
            printf '  [MISSING] %s (set %s or add it to PATH)\n' "$command_name" "$override"
            failures=$((failures + 1))
        fi
    done <<'EOF'
CROSS_CC aarch64-linux-musl-gcc
CROSS_CXX aarch64-linux-musl-g++
CROSS_AR aarch64-linux-musl-ar
CROSS_RANLIB aarch64-linux-musl-ranlib
EOF

    if python3 -c 'from PIL import Image' >/dev/null 2>&1; then
        printf '  [OK]      Python Pillow\n'
    else
        printf '  [MISSING] Python Pillow\n'
        failures=$((failures + 1))
    fi

    local ncnn_source pnnx zephyr_base onnx
    ncnn_source="$(find_ncnn_source || true)"
    pnnx="$(find_pnnx || true)"
    zephyr_base="$(find_zephyr_base || true)"
    onnx="$(find_yolo_onnx || true)"
    if [[ -n "$ncnn_source" ]] &&
        [[ "$(git -C "$ncnn_source" rev-parse HEAD 2>/dev/null || true)" == "$ncnn_revision" ]]; then
        printf '  [OK]      ncnn source %s\n' "$ncnn_revision"
    else
        printf '  [MISSING] pinned ncnn source; set NCNN_SOURCE (commit %s)\n' "$ncnn_revision"
        failures=$((failures + 1))
    fi
    if [[ -n "$pnnx" ]]; then
        printf '  [OK]      pnnx 20260526 candidate (%s)\n' "$pnnx"
    else
        printf '  [MISSING] pnnx 20260526; set PNNX\n'
        failures=$((failures + 1))
    fi
    if [[ -n "$zephyr_base" ]] &&
        [[ "$(zephyr_source_revision "$zephyr_base")" == "$zephyr_revision" ]]; then
        printf '  [OK]      Zephyr source %s\n' "$zephyr_revision"
    else
        printf '  [MISSING] pinned Zephyr source; set ZEPHYR_BASE (commit %s)\n' "$zephyr_revision"
        failures=$((failures + 1))
    fi
    if [[ -s "$onnx" ]] &&
        [[ "$(sha256sum "$onnx" | awk '{print $1}')" == \
            "634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b" ]]; then
        printf '  [OK]      pinned YOLO ONNX\n'
    else
        printf '  [MISSING] YOLO_ONNX with SHA256 634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b\n'
        failures=$((failures + 1))
    fi

    if ((failures > 0)); then
        cat <<'EOF'

Install common Ubuntu dependencies with:
  sudo apt-get update
  sudo apt-get install build-essential cmake ninja-build qemu-system-arm qemu-user \
    e2fsprogs device-tree-compiler python3 python3-pil git curl xz-utils

The AArch64 musl cross compiler is not Ubuntu's native musl-tools package.
Install an aarch64-linux-musl toolchain, then either add its bin directory to
PATH or export CROSS_ROOT=/path/to/aarch64-linux-musl-cross.

Downloaded sources may be reused. Compiled outputs are removed and rebuilt by
`task123.sh build full`. See scripts/competition/README-task123.md for the
pinned source setup commands.
EOF
        return 1
    fi
    printf '\nDOCTOR_PASS\n'
}

fresh_output_dir() {
    local directory="$1"
    case "$directory" in
        "$repo_root/tmp/task3-yolo/"*|\
        "$repo_root/tmp/net-dual-guest/"*|\
        "$repo_root/tmp/starry-task1-periodic") ;;
        *) printf 'error: refusing to clear unexpected output path: %s\n' "$directory" >&2; return 1 ;;
    esac
    rm -rf -- "$directory"
    mkdir -p "$directory"
}

fresh_release_dir() {
    local directory="$1"
    case "$directory" in
        "$repo_root/target/aarch64-unknown-none-softfloat/release"|\
        "$repo_root/target/aarch64-unknown-linux-musl/release") ;;
        *) printf 'error: refusing to clear unexpected release path: %s\n' "$directory" >&2; return 1 ;;
    esac
    rm -rf -- "$directory"
}

build_quick() {
    (cd "$repo_root" && bash scripts/test/net-dual-guest/run-ci-regression.sh)
}

build_full() {
    doctor
    configure_cross_tools
    local ncnn_source pnnx zephyr_base onnx
    ncnn_source="$(find_ncnn_source)"
    pnnx="$(find_pnnx)"
    zephyr_base="$(find_zephyr_base)"
    onnx="$(find_yolo_onnx)"

    # These images are built through nested/custom Cargo workspaces, for which
    # `cargo clean -p ...` at the repository root can report "Removed 0 files".
    # Remove the exact release roots instead so acceptance never reuses an old
    # StarryOS or AxVisor image. Downloaded sources and toolchains stay intact.
    fresh_release_dir "$repo_root/target/aarch64-unknown-none-softfloat/release"
    fresh_release_dir "$repo_root/target/aarch64-unknown-linux-musl/release"
    rm -rf -- "$repo_root/target/starryos-task2-rust"
    build_quick
    fresh_output_dir "$repo_root/tmp/task3-yolo/ncnn-aarch64"
    NCNN_SOURCE="$ncnn_source" \
        "$repo_root/scripts/task3/build-ncnn-aarch64.sh"

    fresh_output_dir "$repo_root/tmp/task3-yolo/ncnn-model"
    YOLO_ONNX="$onnx" PNNX="$pnnx" \
        "$repo_root/scripts/task3/convert-yolo-ncnn.sh"
    "$repo_root/scripts/task3/prepare-yolo-ncnn-input.sh"
    "$repo_root/scripts/task3/prepare-yolo-ncnn-ab-inputs.sh"

    fresh_output_dir "$repo_root/tmp/task3-yolo/ncnn-smoke"
    "$repo_root/scripts/task3/run-ncnn-smoke.sh"

    local variant fault
    while read -r variant fault; do
        fresh_output_dir "$repo_root/tmp/net-dual-guest/zephyr-task2-starry-$variant"
        ZEPHYR_BASE="$zephyr_base" TASK2_ZEPHYR_VIRTIO_SLOT=0 \
            TASK2_FAULT_MODE="$fault" \
            OUT_DIR="$repo_root/tmp/net-dual-guest/zephyr-task2-starry-$variant" \
            BUILD_DIR="$repo_root/tmp/net-dual-guest/zephyr-task2-starry-$variant/cargo-target" \
            "$repo_root/scripts/test/net-dual-guest/build-zephyr-task2.sh"
    done <<'EOF'
normal none
drop-ack drop-ack-once
retry-exhausted drop-ack-always
EOF

    # The checked-in baseline VM config consumes the canonical Zephyr path.
    # Install this build's normal variant there; do not depend on an artifact
    # left by an earlier checkout or validation run.
    fresh_output_dir "$repo_root/tmp/net-dual-guest/zephyr-task2"
    cp "$repo_root/tmp/net-dual-guest/zephyr-task2-starry-normal/zephyr-task2.bin" \
        "$repo_root/tmp/net-dual-guest/zephyr-task2/zephyr-task2.bin"
    cp "$repo_root/tmp/net-dual-guest/zephyr-task2-starry-normal/manifest.toml" \
        "$repo_root/tmp/net-dual-guest/zephyr-task2/manifest.toml"

    fresh_output_dir "$repo_root/tmp/starry-task1-periodic"
    ZEPHYR_BASE="$zephyr_base" \
        OUT_DIR="$repo_root/tmp/starry-task1-periodic" \
        BUILD_DIR="$repo_root/tmp/starry-task1-periodic/cargo-target" \
        "$repo_root/scripts/test/rt-partition/build-zephyr-periodic.sh"

    (cd "$repo_root" && cargo xtask starry rootfs --arch aarch64)
    (cd "$repo_root" && cargo xtask starry app qemu \
        --test-case starryos-task2 --arch aarch64 \
        --qemu-config scripts/competition/qemu-aarch64-starry-build-smoke.toml)
    local starry_elf rootfs
    starry_elf="$repo_root/target/aarch64-unknown-none-softfloat/release/starryos"
    rootfs="$repo_root/tmp/axbuild/rootfs/rootfs-aarch64-alpine.img"
    if ! grep -aFq 'registered virtio network device' "$starry_elf"; then
        printf 'error: freshly built StarryOS image does not contain the virtio-net driver\n' >&2
        return 1
    fi
    # App staging updates ext4 through debugfs. Finish its metadata repair now,
    # before any scenario makes a disposable copy of the image.
    local fsck_status=0
    e2fsck -fy "$rootfs" || fsck_status=$?
    if ((fsck_status > 1)); then
        printf 'error: failed to repair freshly staged rootfs (e2fsck=%d)\n' \
            "$fsck_status" >&2
        return 1
    fi
    e2fsck -fn "$rootfs"
    local build_vm_dir build_rtos_vm
    build_vm_dir="$repo_root/tmp/competition-task123/build"
    build_rtos_vm="$build_vm_dir/vm-aarch64-p2-switch-rtos.runtime.toml"
    mkdir -p "$build_vm_dir"
    python3 "$repo_root/scripts/test/net-dual-guest/render_vm_entry.py" \
        "$repo_root/tmp/net-dual-guest/zephyr-task2/manifest.toml" \
        "$repo_root/scripts/test/net-dual-guest/vm-aarch64-p2-switch-rtos.toml" \
        "$build_rtos_vm"
    (cd "$repo_root" && cargo xtask axvisor build \
        --config scripts/test/net-dual-guest/axvisor-qemu-debug.toml \
        --vmconfigs scripts/test/net-dual-guest/vm-aarch64-starry-switch.toml \
        --vmconfigs "$build_rtos_vm")
    printf 'TASK123_FULL_BUILD_PASS\n'
}

new_evidence_path() {
    local label="$1" stamp
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    printf '%s/%s-%s-%s\n' "$evidence_root" "$stamp" "$label" "$$"
}

run_scenario() {
    local scenario="$1" output_dir="${2:-}"
    if [[ "${ALLOW_DIRTY:-0}" != 1 ]] &&
        [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
        printf 'error: worktree changes exist; commit them or set ALLOW_DIRTY=1 for non-final evidence\n' >&2
        return 1
    fi
    if [[ -z "$output_dir" ]]; then
        output_dir="$(new_evidence_path "$scenario")"
    fi
    mkdir -p "$(dirname "$output_dir")"
    printf 'TASK123_SCENARIO_START name=%s evidence=%s\n' "$scenario" "$output_dir"
    case "$scenario" in
        ci-contracts)
            mkdir -p "$output_dir"
            (cd "$repo_root" && bash scripts/test/net-dual-guest/run-ci-regression.sh) \
                2>&1 | tee "$output_dir/run.log"
            ;;
        task3-yolo-smoke)
            mkdir -p "$output_dir"
            OUT_DIR="$output_dir/build" \
                "$repo_root/scripts/task3/run-ncnn-smoke.sh" \
                2>&1 | tee "$output_dir/run.log"
            ;;
        task1-scheduler-ab)
            "$repo_root/scripts/test/net-dual-guest/run-starry-task1-periodic-ab.sh" "$output_dir"
            ;;
        task23-integrated)
            "$repo_root/scripts/test/net-dual-guest/run-starry-task23-scenario.sh" \
                normal "$output_dir"
            ;;
        task2-normal|task2-drop-ack|task2-retry-exhausted|task2-blackout|task2-out-of-order|task2-invalid-parameter)
            local internal_scenario="${scenario#task2-}"
            STARRY_TASK23_SCOPE=task2 \
                "$repo_root/scripts/test/net-dual-guest/run-starry-task23-scenario.sh" \
                "$internal_scenario" "$output_dir"
            ;;
        task3-model-rejected)
            "$repo_root/scripts/test/net-dual-guest/run-starry-task23-scenario.sh" \
                model-rejected "$output_dir"
            ;;
        *)
            printf 'error: unknown scenario: %s\n' "$scenario" >&2
            list_scenarios >&2
            return 2
            ;;
    esac
    write_task123_source_identity "$repo_root" "$output_dir"
    printf 'TASK123_SCENARIO_PASS name=%s evidence=%s\n' "$scenario" "$output_dir"
}

run_suite() {
    local suite="$1" suite_dir scenario
    local -a scenarios
    case "$suite" in
        task1)
            suite_dir="$(new_evidence_path "suite-$suite")"
            "$repo_root/scripts/competition/run-starry-task1-qemu-matrix.sh" "$suite_dir"
            printf 'TASK123_SUITE_PASS name=%s evidence=%s\n' "$suite" "$suite_dir"
            return
            ;;
        task2)
            scenarios=(
                task2-normal task2-drop-ack task2-retry-exhausted
                task2-blackout task2-out-of-order task2-invalid-parameter
            )
            ;;
        task3)
            scenarios=(task3-yolo-smoke task3-model-rejected)
            ;;
        quick) scenarios=(ci-contracts task3-yolo-smoke) ;;
        acceptance|video)
            scenarios=(task1-scheduler-ab task2-normal task2-blackout task23-integrated task3-model-rejected)
            ;;
        full)
            scenarios=(
                task3-yolo-smoke task1-scheduler-ab task2-normal task23-integrated
                task2-drop-ack task2-retry-exhausted task2-blackout
                task2-out-of-order task2-invalid-parameter task3-model-rejected
            )
            ;;
        *) printf 'error: unknown suite: %s\n' "$suite" >&2; return 2 ;;
    esac
    suite_dir="$(new_evidence_path "suite-$suite")"
    mkdir -p "$suite_dir"
    for scenario in "${scenarios[@]}"; do
        run_scenario "$scenario" "$suite_dir/$scenario"
    done
    printf 'TASK123_SUITE_PASS name=%s evidence=%s\n' "$suite" "$suite_dir"
}

require_positive_integer() {
    local name="$1" value="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        printf 'error: %s must be a positive integer: %s\n' "$name" "$value" >&2
        return 2
    fi
}

board_ram_boot() {
    local fit="$1" log="$2" ready_regex="$3" timeout="$4"
    [[ -f "$fit" ]] || {
        printf 'error: board FIT is missing: %s\n' "$fit" >&2
        return 1
    }
    mkdir -p "$(dirname "$log")"
    ATK_PORT="${TASK123_BOARD_PORT:-/dev/ttyACM0}" \
    ATK_BAUD="${TASK123_BOARD_BAUD:-1500000}" \
    ATK_FASTBOOT_SN="${TASK123_BOARD_FASTBOOT_SN:-}" \
    ATK_BREAK_WINDOW="${TASK123_BOARD_BREAK_WINDOW:-300}" \
    ATK_LOG="$log" \
    ATK_READY_REGEX="$ready_regex" \
    ATK_READY_TIMEOUT="$timeout" \
        "$repo_root/scripts/board/atk-dlrk3588-ram-boot.sh" "$fit"
}

board_native() {
    local runs="${TASK123_BOARD_RUNS:-3}" run
    local output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task1/native-zephyr-board-$(date +%Y%m%d)}"
    local input_root="${TASK123_NATIVE_INPUT_DIR:-$repo_root/results/task1/native-zephyr-board-20260825/build}"
    require_positive_integer TASK123_BOARD_RUNS "$runs"
    for ((run = 1; run <= runs; run++)); do
        NATIVE_ZEPHYR_INPUT_DIR="$input_root" \
        NATIVE_ZEPHYR_OUTPUT_DIR="$output_root/run-$(printf '%02d' "$run")" \
        NATIVE_ZEPHYR_SAMPLES="${TASK123_BOARD_SAMPLES:-6000}" \
        NATIVE_ZEPHYR_TIMEOUT_SEC="${TASK123_BOARD_TIMEOUT_SEC:-180}" \
        ATK_PORT="${TASK123_BOARD_PORT:-/dev/ttyACM0}" \
        ATK_BAUD="${TASK123_BOARD_BAUD:-1500000}" \
        ATK_FASTBOOT_SN="${TASK123_BOARD_FASTBOOT_SN:-}" \
        ATK_BREAK_WINDOW="${TASK123_BOARD_BREAK_WINDOW:-300}" \
            "$repo_root/scripts/board/run-atk-native-zephyr.sh"
    done
}

board_task1_matrix() {
    local topology="$1" runs="${TASK123_BOARD_RUNS:-3}" samples="${TASK123_BOARD_SAMPLES:-6000}"
    local period_ms="${TASK123_TASK1_PERIOD_MS:-10}" scheduler run fit boot_log arm_log
    local default_artifacts output_root
    case "$topology" in
    communication-share)
        default_artifacts="$repo_root/results/task1/board-20260825/communication-share-sustained-v8/artifacts"
        ;;
    ai-share)
        default_artifacts="$repo_root/results/task1/board-20260825/ai-share-6000-v1/artifacts"
        ;;
    *) printf 'error: unsupported Task 1 topology: %s\n' "$topology" >&2; return 2 ;;
    esac
    local artifacts="${TASK123_BOARD_ARTIFACT_DIR:-$default_artifacts}"
    output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task1/board-$(date +%Y%m%d)/$topology-matrix}"
    require_positive_integer TASK123_BOARD_RUNS "$runs"
    require_positive_integer TASK123_BOARD_SAMPLES "$samples"
    require_positive_integer TASK123_TASK1_PERIOD_MS "$period_ms"
    for scheduler in rr fp-rr; do
        fit="$artifacts/axvisor-task123-zephyr-$scheduler.fit"
        for ((run = 1; run <= runs; run++)); do
            boot_log="$output_root/$scheduler-run$(printf '%02d' "$run")-boot.log"
            arm_log="$output_root/$scheduler-run$(printf '%02d' "$run").log"
            board_ram_boot "$fit" "$boot_log" '\[OK\] Default guest initialized' \
                "${TASK123_BOARD_BOOT_TIMEOUT_SEC:-180}"
            python3 "$repo_root/scripts/board/run-atk-task1-yolo-arm.py" \
                --port "${TASK123_BOARD_PORT:-/dev/ttyACM0}" \
                --baud "${TASK123_BOARD_BAUD:-1500000}" \
                --scheduler "$scheduler" \
                --runtime-seconds "${TASK123_TASK1_RUNTIME_SEC:-$((samples * period_ms / 1000))}" \
                --load-mode yolo \
                --workload-start payload-init \
                --expected-inferences "${TASK123_TASK1_INFERENCES:-10}" \
                --expected-samples "$samples" \
                --period-ms "$period_ms" \
                --dump-chunk-rows "${TASK123_TASK1_DUMP_CHUNK_ROWS:-256}" \
                --periodic-guest zephyr \
                --artifact "$fit" \
                "$arm_log"
        done
    done
}

board_task2_throughput() {
    local runs="${TASK123_BOARD_RUNS:-3}" transactions="${TASK123_TASK2_TRANSACTIONS:-200}" run
    local fit="${TASK123_BOARD_TASK2_FIT:-$repo_root/results/task2/board-20260825/bounded-200/artifacts/axvisor-task123-zephyr-fp-rr.fit}"
    local output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task2/board-$(date +%Y%m%d)/bounded-$transactions}"
    require_positive_integer TASK123_BOARD_RUNS "$runs"
    require_positive_integer TASK123_TASK2_TRANSACTIONS "$transactions"
    mkdir -p "$output_root"
    for ((run = 1; run <= runs; run++)); do
        board_ram_boot "$fit" "$output_root/fp-rr-run$run.log" \
            "TASK2_BENCHMARK_END transactions=$transactions pending=0 errors=0" \
            "${TASK123_BOARD_TIMEOUT_SEC:-300}"
        python3 "$repo_root/scripts/task2/quantify-benchmark.py" \
            "$output_root/fp-rr-run$run.log" --transactions "$transactions" \
            --out "$output_root/fp-rr-run$run.json"
    done
}

board_task3_matrix() {
    local runs="${TASK123_BOARD_RUNS:-3}" run mode fit end_regex
    local fixed_artifacts="${TASK123_TASK3_FIXED_ARTIFACT_DIR:-$repo_root/results/task3/board-20260825/fresh-build-v2/fixed/artifacts}"
    local rknn_artifacts="${TASK123_TASK3_RKNN_ARTIFACT_DIR:-$repo_root/results/task3/board-20260825/fresh-build-v2/rknn/artifacts}"
    local output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task3/board-$(date +%Y%m%d)/fixed-vs-rknn}"
    require_positive_integer TASK123_BOARD_RUNS "$runs"
    mkdir -p "$output_root"
    for ((run = 1; run <= runs; run++)); do
        for mode in fixed rknn; do
            if [[ "$mode" == fixed ]]; then
                fit="$fixed_artifacts/axvisor-task123-zephyr-fp-rr.fit"
                end_regex='TASK3_HYBRID_SCENE_END source=fixed-perception controller_rc=0 producer_rc=na'
            else
                fit="$rknn_artifacts/axvisor-task123-zephyr-fp-rr.fit"
                end_regex='TASK3_HYBRID_SCENE_END source=rknn controller_complete=1 producer_rc=0'
            fi
            board_ram_boot "$fit" "$output_root/$mode-fp-rr-run$run.log" "$end_regex" \
                "${TASK123_BOARD_TIMEOUT_SEC:-420}"
        done
        python3 "$repo_root/scripts/task3/quantify-hybrid-scene.py" \
            --fixed "$output_root/fixed-fp-rr-run$run.log" \
            --rknn "$output_root/rknn-fp-rr-run$run.log" \
            --out "$output_root/report-run$run"
    done
}

board_task3_build() {
    local output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task3/board-$(date +%Y%m%d)/fresh-build}"
    BUSYBOX_STATIC="${TASK123_BUSYBOX_STATIC:-${BUSYBOX_STATIC:-}}" \
    RKNN_BUNDLE="${TASK123_RKNN_BUNDLE:-${RKNN_BUNDLE:-}}" \
    ATK_TASK1_TOPOLOGY="${TASK123_TASK3_TOPOLOGY:-communication-share}" \
    TASK1_ZEPHYR_SAMPLE_COUNT="${TASK123_BOARD_SAMPLES:-300}" \
    TASK1_ZEPHYR_DUMP_CHUNK_ROWS="${TASK123_TASK1_DUMP_CHUNK_ROWS:-256}" \
        "$repo_root/scripts/board/build-atk-task3-matrix.sh" "$output_root"
}

board_task3_all() {
    local output_root="${TASK123_BOARD_OUTPUT_DIR:-$repo_root/results/task3/board-$(date +%Y%m%d)/fresh-full}"
    TASK123_BOARD_OUTPUT_DIR="$output_root/build" board_task3_build
    TASK123_TASK3_FIXED_ARTIFACT_DIR="$output_root/build/fixed/artifacts" \
    TASK123_TASK3_RKNN_ARTIFACT_DIR="$output_root/build/rknn/artifacts" \
    TASK123_BOARD_OUTPUT_DIR="$output_root/runs" board_task3_matrix
}

board_demo() {
    local task2_root="${TASK123_TASK2_EVIDENCE_DIR:-$repo_root/results/task2/board-20260825/bounded-200}"
    local task3_root="${TASK123_TASK3_EVIDENCE_DIR:-$repo_root/results/task3/board-20260825/fresh-build-v2-runs}"
    local rejection_log="${TASK123_TASK3_REJECTION_LOG:-$repo_root/results/atk-dlrk3588-task123-integrated-ab-20260824/logs/yolo-console.log}"
    local task3_demo="${TASK123_TASK3_DEMO_DIR:-$task3_root/demo}"
    local scene_cpio="${TASK123_TASK3_SCENE_CPIO:-$repo_root/results/task3/board-20260825/fresh-build-v2/rknn/payload-rknn.cpio}"
    [[ -f "$scene_cpio" ]] || {
        printf 'error: Task 3 fresh scene cpio is missing: %s\n' "$scene_cpio" >&2
        return 1
    }
    mkdir -p "$task3_demo/scene-images"
    (
        cd "$task3_demo/scene-images"
        cpio -idu --quiet 'rknn/validation/road-*.jpg' 'rknn/validation/hazard-*.jpg' \
            < "$scene_cpio"
    )
    python3 "$repo_root/scripts/competition/render-task123-demo.py" task2 \
        --log "${TASK123_TASK2_DEMO_LOG:-$task2_root/fp-rr-run3.log}" \
        --transactions "${TASK123_TASK2_TRANSACTIONS:-200}" \
        --out "${TASK123_TASK2_DEMO_DIR:-$task2_root/demo}"
    python3 "$repo_root/scripts/competition/render-task123-demo.py" task3 \
        --fixed "${TASK123_TASK3_FIXED_LOG:-$task3_root/fixed-fp-rr-run2.log}" \
        --rknn "${TASK123_TASK3_RKNN_LOG:-$task3_root/rknn-fp-rr-run2.log}" \
        --rejection-log "$rejection_log" \
        --out "$task3_demo"
}

board_demo_video() {
    local task2_root="${TASK123_TASK2_EVIDENCE_DIR:-$repo_root/results/task2/board-20260825/bounded-200}"
    local task3_root="${TASK123_TASK3_EVIDENCE_DIR:-$repo_root/results/task3/board-20260825/fresh-build-v2-runs}"
    local task2_demo="${TASK123_TASK2_DEMO_DIR:-$task2_root/demo}"
    local task3_demo="${TASK123_TASK3_DEMO_DIR:-$task3_root/demo}"
    board_demo
    python3 "$repo_root/scripts/competition/record-task123-demo.py" \
        --dashboard "$task2_demo/dashboard.html" \
        --out "$task2_demo/dashboard.mp4" \
        --frames "${TASK123_DEMO_FRAMES:-12}" \
        --capture-fps "${TASK123_DEMO_CAPTURE_FPS:-2}"
    python3 "$repo_root/scripts/competition/record-task123-demo.py" \
        --dashboard "$task3_demo/dashboard.html" \
        --out "$task3_demo/dashboard.mp4" \
        --frames "${TASK123_DEMO_FRAMES:-12}" \
        --safe-frames "${TASK123_TASK3_DEMO_SAFE_FRAMES:-3}" \
        --capture-fps "${TASK123_DEMO_CAPTURE_FPS:-2}"
}

run_board_command() {
    local command="$1"
    case "$command" in
        native) board_native ;;
        task1-communication) board_task1_matrix communication-share ;;
        task1-ai)
            if [[ "${TASK123_ALLOW_AI_SHARE_ABLATION:-0}" != 1 ]]; then
                printf '%s\n' 'error: task1-ai is a non-official ablation; set TASK123_ALLOW_AI_SHARE_ABLATION=1 only for an explicitly labelled diagnostic run' >&2
                return 2
            fi
            board_task1_matrix ai-share
            ;;
        task2-throughput) board_task2_throughput ;;
        task3-build) board_task3_build ;;
        task3-matrix) board_task3_matrix ;;
        task3-all) board_task3_all ;;
        demo) board_demo ;;
        demo-video) board_demo_video ;;
        *) printf 'error: unknown physical-board command: %s\n' "$command" >&2; list_scenarios >&2; return 2 ;;
    esac
}

main() {
    local command="${1:-}"
    configure_cross_tools
    case "$command" in
        doctor) [[ $# -eq 1 ]] || { usage >&2; return 2; }; doctor ;;
        prepare)
            [[ $# -eq 1 ]] || { usage >&2; return 2; }
            "$repo_root/scripts/competition/prepare-task123-deps.sh"
            ;;
        --list|list) [[ $# -eq 1 ]] || { usage >&2; return 2; }; list_scenarios ;;
        build)
            [[ $# -le 2 ]] || { usage >&2; return 2; }
            case "${2:-full}" in
                quick) build_quick ;;
                full) build_full ;;
                *) usage >&2; return 2 ;;
            esac
            ;;
        run) [[ $# -eq 2 ]] || { usage >&2; return 2; }; run_scenario "$2" ;;
        suite) [[ $# -eq 2 ]] || { usage >&2; return 2; }; run_suite "$2" ;;
        board) [[ $# -eq 2 ]] || { usage >&2; return 2; }; run_board_command "$2" ;;
        -h|--help) usage ;;
        *) usage >&2; return 2 ;;
    esac
}

main "$@"
