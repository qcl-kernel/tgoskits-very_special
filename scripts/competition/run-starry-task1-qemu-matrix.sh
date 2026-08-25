#!/usr/bin/env bash
set -euo pipefail

# One-command QEMU evidence matrix for the real StarryOS Task 1 topology.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
output_root="${1:?usage: run-starry-task1-qemu-matrix.sh OUTPUT_ROOT}"
zephyr_revision="dccb09599635bdff17633fa7e9dab014b91dce90"
deps_root="${TASK123_DEPS_DIR:-$repo_root/.deps/task123}"
cross_root="${CROSS_ROOT:-$deps_root/aarch64-linux-musl-cross}"
zephyr_base="${ZEPHYR_BASE:-$deps_root/zephyr-$zephyr_revision}"
runner="$repo_root/scripts/test/net-dual-guest/run-starry-task1-periodic-ab.sh"
sample_count="${TASK1_SAMPLE_COUNT:-6000}"
dump_chunk_rows="${TASK1_DUMP_CHUNK_ROWS:-256}"

[[ "$sample_count" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: TASK1_SAMPLE_COUNT must be a positive integer\n' >&2
    exit 2
}
[[ "$dump_chunk_rows" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: TASK1_DUMP_CHUNK_ROWS must be a positive integer\n' >&2
    exit 2
}

[[ ! -e "$output_root" ]] || {
    printf 'error: output already exists: %s\n' "$output_root" >&2
    exit 2
}

"$repo_root/scripts/competition/prepare-task123-deps.sh"
mkdir -p "$output_root"

build_probe() {
    local samples="$1" output="$2"
    CROSS_ROOT="$cross_root" \
        CROSS_COMPILE="$cross_root/bin/aarch64-linux-musl-" \
        ZEPHYR_BASE="$zephyr_base" \
        ZEPHYR_SOURCE_REVISION="$zephyr_revision" \
        ZEPHYR_SAMPLE_COUNT="$samples" \
        ZEPHYR_DUMP_GATED=1 \
        ZEPHYR_DUMP_CHUNK_ROWS="$dump_chunk_rows" \
        OUT_DIR="$output" \
        BUILD_DIR="$output/build" \
        "$repo_root/scripts/test/rt-partition/build-zephyr-periodic.sh"
}

probe="$output_root/probe-$sample_count"
build_probe "$sample_count" "$probe"

cat > "$output_root/protocol.txt" <<EOF
target=qemu-aarch64
topology=axvisor+starryos+zephyr
idle_ab=rr:1x$sample_count,fp-rr:1x$sample_count
pressure_ab=rr:1x$sample_count,fp-rr:1x$sample_count
pressure=real-starryos-ncnn-yolo
period_ms=10
evidence_dump=host-acknowledged-chunks
dump_chunk_rows=$dump_chunk_rows
EOF

ALLOW_DIRTY="${ALLOW_DIRTY:-0}" \
    STARRY_TASK1_PERIODIC_DIR="$probe" \
    STARRY_TASK1_PERIODIC_ARMS=rr,fp-rr \
    STARRY_TASK1_PERIODIC_REPEATS=1 \
    STARRY_TASK1_LOAD_MODE=idle \
    "$runner" "$output_root/idle-ab-$sample_count"

ALLOW_DIRTY="${ALLOW_DIRTY:-0}" \
    STARRY_TASK1_PERIODIC_DIR="$probe" \
    STARRY_TASK1_PERIODIC_ARMS=rr,fp-rr \
    STARRY_TASK1_PERIODIC_REPEATS=1 \
    STARRY_TASK1_LOAD_MODE=yolo \
    "$runner" "$output_root/pressure-ab-$sample_count"

write_task123_source_identity "$repo_root" "$output_root"
find "$output_root" -type f ! -name SHA256SUMS.txt -print0 \
    | sort -z | xargs -0 sha256sum > "$output_root/SHA256SUMS.txt"
printf 'PASS: complete StarryOS Task 1 QEMU matrix retained in %s\n' "$output_root"
