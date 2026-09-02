#!/usr/bin/env bash
set -euo pipefail

# Original two-pCPU Task 1 idle/pressure QEMU evidence matrix.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
output_root="${1:?usage: run-starry-task1-qemu-matrix.sh OUTPUT_ROOT}"
runner="$repo_root/scripts/test/net-dual-guest/run-starry-task1-periodic-ab.sh"
probe="${STARRY_TASK1_PERIODIC_DIR:-$repo_root/tmp/starry-task1-periodic}"
manifest="$probe/zephyr-periodic.manifest"

[[ ! -e "$output_root" ]] || {
    printf 'error: output already exists: %s\n' "$output_root" >&2
    exit 2
}

[[ -s "$manifest" ]] || {
    printf 'error: Task 1 build artifact is missing: %s; run task123.sh build task1 first\n' \
        "$manifest" >&2
    exit 1
}
sample_count="$(awk -F= '$1 == "sample_count" { print $2 }' "$manifest")"
dump_chunk_rows="$(awk -F= '$1 == "dump_chunk_rows" { print $2 }' "$manifest")"
[[ "$sample_count" =~ ^[1-9][0-9]*$ && "$dump_chunk_rows" =~ ^[1-9][0-9]*$ ]] || {
    printf 'error: invalid Task 1 build manifest: %s\n' "$manifest" >&2
    exit 1
}
mkdir -p "$output_root"

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
