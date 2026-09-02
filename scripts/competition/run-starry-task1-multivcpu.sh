#!/usr/bin/env bash
set -euo pipefail

# Run the multi-vCPU matrix from artifacts produced by task123.sh build task1-multivcpu.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_root="${1:?usage: run-starry-task1-multivcpu.sh OUTPUT_ROOT}"
zephyr_dir="${STARRY_TASK1_TOPOLOGY_ZEPHYR_DIR:-$repo_root/tmp/starry-task1-multivcpu}"
[[ ! -e "$output_root" ]] || {
    printf 'error: output already exists: %s\n' "$output_root" >&2
    exit 2
}

ALLOW_DIRTY="${ALLOW_DIRTY:-0}" \
    STARRY_TASK1_TOPOLOGY_ZEPHYR_DIR="$zephyr_dir" \
    "$repo_root/scripts/test/net-dual-guest/run-starry-task1-multivcpu.sh" \
        "$output_root/matrix"

printf 'PASS: complete Task 1 multi-vCPU QEMU evidence retained in %s\n' "$output_root"
