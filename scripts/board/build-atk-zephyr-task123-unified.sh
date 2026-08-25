#!/usr/bin/env bash
# Build one Zephyr 4.4.2 Task 1/2/3 Guest and embed that exact binary in the
# AxVisor RR and FP-RR variants for the ATK-DLRK3588 physical board.
#
# This script only produces host-side artifacts. It never opens the board,
# invokes fastboot, or writes eMMC.

set -euo pipefail

readonly ZEPHYR_COMMIT=dccb09599635bdff17633fa7e9dab014b91dce90

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
template_dir="$repo_root/scripts/board/task123-zephyr"
output_dir=""
starry_kernel="${STARRY_KERNEL:-$repo_root/target/aarch64-unknown-none-softfloat/release/starryos.bin}"
starry_initrd="${STARRY_INITRD:-$repo_root/tmp/atk-task123-integrated-ab-20260824/task2-linux-initramfs.cpio.gz}"
host_dtb="${ATK_HOST_DTB:-${TASK123_BOARD_DTB:-}}"
zephyr_base="${ZEPHYR_BASE:-$repo_root/.deps/task123/zephyr-$ZEPHYR_COMMIT}"
sample_count="${TASK1_ZEPHYR_SAMPLE_COUNT:-300}"
dump_chunk_rows="${TASK1_ZEPHYR_DUMP_CHUNK_ROWS:-256}"
topology="${ATK_TASK1_TOPOLOGY:-communication-share}"
starry_vcpu0_pcpu=""
starry_vcpu1_pcpu=""
axvisor_elf="$repo_root/target/aarch64-unknown-linux-musl/release/axvisor"

main() {
    parse_arguments "$@"
    resolve_topology
    require_inputs
    normalize_input_paths
    prepare_directories
    build_unified_zephyr_guest
    materialize_guest_configs
    build_scheduler_variant rr rr-scheduler RR
    build_scheduler_variant fp-rr fp-rr-scheduler FP-RR
    write_integrity_manifest
    printf 'unified Task 1/2/3 artifacts: %s\n' "$output_dir"
    printf 'Zephyr Guest SHA256: %s\n' "$(sha256sum "$output_dir/inputs/zephyr/zephyr-task2.bin" | awk '{print $1}')"
    printf 'Hybrid topology: %s; StarryOS vCPU0->%s, vCPU1->%s; Zephyr vCPU0->pCPU1; NPU->StarryOS\n' \
        "$topology" "$starry_vcpu0_pcpu" "$starry_vcpu1_pcpu"
    printf 'RR FIT: %s\n' "$output_dir/axvisor-task123-zephyr-rr.fit"
    printf 'FP-RR FIT: %s\n' "$output_dir/axvisor-task123-zephyr-fp-rr.fit"
}

resolve_topology() {
    case "$topology" in
    communication-share)
        # Primary/AI vCPU0 has pCPU2; communication vCPU1 shares pCPU1.
        starry_vcpu0_pcpu=0x200
        starry_vcpu1_pcpu=0x100
        ;;
    ai-share)
        # Strong-competition arm: primary/AI vCPU0 shares pCPU1 while the
        # communication vCPU moves to pCPU2. Guest-local taskset roles stay
        # unchanged so only physical placement differs.
        starry_vcpu0_pcpu=0x100
        starry_vcpu1_pcpu=0x200
        ;;
    *)
        printf 'error: ATK_TASK1_TOPOLOGY must be communication-share or ai-share: %s\n' \
            "$topology" >&2
        exit 2
        ;;
    esac
}

normalize_input_paths() {
    starry_kernel="$(realpath "$starry_kernel")"
    starry_initrd="$(realpath "$starry_initrd")"
    host_dtb="$(realpath "$host_dtb")"
    zephyr_base="$(realpath "$zephyr_base")"
}

parse_arguments() {
    if [[ $# -ne 1 ]]; then
        printf 'usage: %s <new-output-directory>\n' "${BASH_SOURCE[0]##*/}" >&2
        exit 2
    fi
    output_dir="$(realpath -m "$1")"
    if [[ -e "$output_dir" && -n "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'error: output directory must be absent or empty: %s\n' "$output_dir" >&2
        exit 2
    fi
}

require_inputs() {
    local path executable actual_commit
    if [[ -z "$host_dtb" ]]; then
        printf 'error: set ATK_HOST_DTB or TASK123_BOARD_DTB to the ATK-DLRK3588 host DTB\n' >&2
        exit 1
    fi
    for path in "$starry_kernel" "$starry_initrd" "$host_dtb"; do
        if [[ ! -f "$path" ]]; then
            printf 'error: required input is missing: %s\n' "$path" >&2
            exit 1
        fi
    done
    if [[ ! -f "$zephyr_base/CMakeLists.txt" ]]; then
        printf 'error: pinned Zephyr source is missing: %s\n' "$zephyr_base" >&2
        exit 1
    fi
    actual_commit="$(task123_source_revision "$zephyr_base" 2>/dev/null || true)"
    if [[ "$actual_commit" != "$ZEPHYR_COMMIT" ]]; then
        printf 'error: expected Zephyr %s, found %s\n' "$ZEPHYR_COMMIT" "$actual_commit" >&2
        exit 1
    fi
    if [[ ! "$sample_count" =~ ^[1-9][0-9]*$ ]]; then
        printf 'error: TASK1_ZEPHYR_SAMPLE_COUNT must be a positive integer\n' >&2
        exit 2
    fi
    if [[ ! "$dump_chunk_rows" =~ ^[1-9][0-9]*$ ]]; then
        printf 'error: TASK1_ZEPHYR_DUMP_CHUNK_ROWS must be a positive integer\n' >&2
        exit 2
    fi
    for executable in cargo cmake ninja aarch64-linux-gnu-objcopy mkimage sha256sum; do
        if ! command -v "$executable" >/dev/null 2>&1; then
            printf 'error: required executable is unavailable: %s\n' "$executable" >&2
            exit 1
        fi
    done
}

prepare_directories() {
    mkdir -p "$output_dir/inputs/zephyr" "$output_dir/build/zephyr" "$output_dir/configs" "$output_dir/logs"
}

build_unified_zephyr_guest() {
    OUT_DIR="$output_dir/inputs/zephyr" \
    BUILD_DIR="$output_dir/build/zephyr" \
    ZEPHYR_BASE="$zephyr_base" \
    TASK2_ZEPHYR_VIRTIO_SLOT=0 \
    TASK2_ZEPHYR_VIRTIO_FDT_PATH=/virtio_mmio@b000000 \
    TASK2_ZEPHYR_VIRTIO_HOST_HWIRQ=1 \
    TASK2_ZEPHYR_VIRTIO_GUEST_IRQ=33 \
    TASK2_ZEPHYR_EXTRA_OVERLAY="$repo_root/scripts/test/net-dual-guest/zephyr-task2/atk-dlrk3588-axvisor.overlay" \
    TASK2_TIMER_FREQUENCY_HZ=24000000 \
    TASK1_ZEPHYR_SAMPLE_COUNT="$sample_count" \
    TASK1_ZEPHYR_DUMP_CHUNK_ROWS="$dump_chunk_rows" \
        "$repo_root/scripts/test/net-dual-guest/build-zephyr-task2.sh" \
        2>&1 | tee "$output_dir/logs/build-zephyr.log"
}

materialize_guest_configs() {
    local zephyr_entry zephyr_binary
    zephyr_entry="$(awk -F' = ' '$1 == "elf_entry" {gsub(/\"/, "", $2); print $2}' "$output_dir/inputs/zephyr/manifest.toml")"
    zephyr_binary="$output_dir/inputs/zephyr/zephyr-task2.bin"
    render_template "$template_dir/starry.toml.in" "$output_dir/configs/atk-task123-zephyr-starry.toml" \
        STARRY_KERNEL "$starry_kernel" STARRY_INITRD "$starry_initrd" \
        STARRY_VCPU0_PCPU "$starry_vcpu0_pcpu" \
        STARRY_VCPU1_PCPU "$starry_vcpu1_pcpu"
    render_template "$template_dir/zephyr.toml.in" "$output_dir/configs/atk-task123-zephyr.toml" \
        ZEPHYR_ENTRY "$zephyr_entry" ZEPHYR_BINARY "$zephyr_binary"
}

build_scheduler_variant() {
    local name="$1" feature="$2" label="$3"
    local board_config="$output_dir/configs/atk-task123-zephyr-$name-board.toml"
    local raw_image="$output_dir/axvisor-task123-zephyr-$name.bin"
    local its="$output_dir/axvisor-task123-zephyr-$name.its"
    local fit="$output_dir/axvisor-task123-zephyr-$name.fit"

    render_template "$template_dir/board.toml.in" "$board_config" SCHEDULER_FEATURE "$feature"
    (
        cd "$repo_root"
        cargo xtask axvisor build \
            --config "$board_config" \
            --vmconfigs "$output_dir/configs/atk-task123-zephyr-starry.toml" \
            --vmconfigs "$output_dir/configs/atk-task123-zephyr.toml"
    ) 2>&1 | tee "$output_dir/logs/build-axvisor-$name.log"
    if [[ ! -f "$axvisor_elf" ]]; then
        printf 'error: AxVisor build did not produce %s\n' "$axvisor_elf" >&2
        exit 1
    fi
    aarch64-linux-gnu-objcopy -O binary "$axvisor_elf" "$raw_image"
    render_template "$template_dir/axvisor.its.in" "$its" \
        SCHEDULER_LABEL "$label" AXVISOR_BINARY "$raw_image" HOST_DTB "$host_dtb"
    mkimage -f "$its" "$fit" | tee "$output_dir/logs/mkimage-$name.log"
}

render_template() {
    local source="$1" destination="$2"
    shift 2
    local expression=() key value
    while (($#)); do
        key="$1"
        value="$(escape_sed_replacement "$2")"
        expression+=("-e" "s|@$key@|$value|g")
        shift 2
    done
    sed "${expression[@]}" "$source" > "$destination"
    if grep -En '@[A-Z0-9_]+@' "$destination" >/dev/null; then
        printf 'error: unresolved template token in %s\n' "$destination" >&2
        exit 1
    fi
}

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

write_integrity_manifest() {
    {
        printf 'topology=%s\n' "$topology"
        printf 'starry_vcpu0_role=ai\n'
        printf 'starry_vcpu0_pcpu=%s\n' "$starry_vcpu0_pcpu"
        printf 'starry_vcpu1_role=communication\n'
        printf 'starry_vcpu1_pcpu=%s\n' "$starry_vcpu1_pcpu"
        printf 'zephyr_vcpu0_pcpu=0x100\n'
    } > "$output_dir/topology.manifest"
    (
        cd "$output_dir"
        find . -type f ! -path './build/*' ! -name SHA256SUMS.txt -print0 \
            | sort -z \
            | xargs -0 sha256sum > SHA256SUMS.txt
    )
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
