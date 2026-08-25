#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
ncnn_prefix="${NCNN_PREFIX:-$repo_root/tmp/task3-yolo/ncnn-aarch64/install}"
model_dir="${TASK3_NCNN_MODEL_DIR:-$repo_root/tmp/task3-yolo/ncnn-model}"
cross_cxx="$(resolve_task123_tool CROSS_CXX aarch64-linux-musl-g++)"
cross_qemu="$(resolve_task123_tool QEMU_AARCH64 qemu-aarch64)"
out_dir="${OUT_DIR:-$repo_root/tmp/task3-yolo/ncnn-smoke}"
input_path="${1:-${TASK3_NCNN_INPUT:-$model_dir/input.ppm}}"
for asset in \
    "$model_dir/yolo11n.ncnn.param" \
    "$model_dir/yolo11n.ncnn.bin" \
    "$input_path"; do
    if [[ ! -f "$asset" ]]; then
        printf 'error: ncnn smoke asset is missing: %s\n' "$asset" >&2
        exit 2
    fi
done
mkdir -p "$out_dir"

"$cross_cxx" -std=c++11 -O2 -static \
    -I"$ncnn_prefix/include" \
    "$repo_root/components/task3-ncnn/src/adapter.cc" \
    "$repo_root/scripts/task3/ncnn-smoke.cc" \
    "$ncnn_prefix/lib/libncnn.a" -lstdc++ -lgcc -lm -lpthread \
    -o "$out_dir/ncnn-smoke"

if [[ ! -x "$cross_qemu" ]]; then
    printf 'error: qemu-aarch64 is missing: %s\n' "$cross_qemu" >&2
    exit 1
fi
"$cross_qemu" "$out_dir/ncnn-smoke" \
    "$model_dir/yolo11n.ncnn.param" \
    "$model_dir/yolo11n.ncnn.bin" \
    "$input_path"
