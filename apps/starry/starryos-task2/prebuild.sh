#!/usr/bin/env bash
set -euo pipefail

app_dir="${STARRY_APP_DIR:?prebuild: STARRY_APP_DIR is required}"
overlay_dir="${STARRY_OVERLAY_DIR:?prebuild: STARRY_OVERLAY_DIR is required}"
arch="${STARRY_ARCH:?prebuild: STARRY_ARCH is required}"
workspace="${STARRY_WORKSPACE:?prebuild: STARRY_WORKSPACE is required}"
source "$workspace/scripts/lib/task123-tools.sh"

case "$arch" in
    aarch64) triple="aarch64-linux-musl" ;;
    *) echo "prebuild: starryos-task2 currently supports only aarch64" >&2; exit 1 ;;
esac

cc="$(resolve_task123_tool CROSS_CC "${triple}-gcc")"
build_scope="${STARRY_TASK23_BUILD_SCOPE:-${STARRY_TASK23_SCOPE:-integrated}}"
case "$build_scope" in
    integrated|task2) ;;
    *)
        echo "prebuild: STARRY_TASK23_BUILD_SCOPE must be integrated or task2" >&2
        exit 2
        ;;
esac

cargo_feature_args=(--no-default-features)
if [[ "$build_scope" == integrated ]]; then
    cxx="$(resolve_task123_tool CROSS_CXX "${triple}-g++")"
    ar="$(resolve_task123_tool CROSS_AR "${triple}-ar")"
    ncnn_prefix="${NCNN_PREFIX:-$workspace/tmp/task3-yolo/ncnn-aarch64/install}"
    yolo_assets="${TASK3_YOLO_ASSETS:-${TASK3_NCNN_MODEL_DIR:-$workspace/tmp/task3-yolo/ncnn-model}}"
    ab_manifest="$workspace/scripts/task3/task3-ab-manifest.tsv"
    ab_assets="$yolo_assets/task3-ab"
    cargo_feature_args=(--features ncnn)
    if [[ ! -f "$ncnn_prefix/include/ncnn/net.h" || ! -f "$ncnn_prefix/lib/libncnn.a" ]]; then
        echo "prebuild: incomplete ncnn installation: $ncnn_prefix" >&2
        exit 1
    fi
fi

verify_asset() {
    local name="$1"
    local expected_sha256="$2"
    local path="$yolo_assets/$name"
    if [[ ! -f "$path" ]]; then
        echo "prebuild: missing YOLO asset: $path" >&2
        exit 1
    fi
    local actual_sha256
    actual_sha256="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "$actual_sha256" != "$expected_sha256" ]]; then
        echo "prebuild: YOLO asset hash mismatch for $name" >&2
        echo "prebuild: expected $expected_sha256, got $actual_sha256" >&2
        exit 1
    fi
}

if [[ "$build_scope" == integrated ]]; then
    verify_asset yolo11n.ncnn.param d2c0adf8939dc9ce02964ce8ada104447768ffd8e3bffad8fa11e2e61e709c1f
    verify_asset yolo11n.ncnn.bin 0ae562447923999779b12b4f91f96b9ef263add8c9902d10e22e6dd6a2932c12
    verify_asset input.ppm 608c8a61ff0bb43e5a8613f1f6f8aa08af74b084363610ed2b526ad925e4cb6f
    YOLO_AB_OUT_DIR="$ab_assets" \
        "$workspace/scripts/task3/prepare-yolo-ncnn-ab-inputs.sh" >/dev/null
fi

build_dir="$workspace/target/starryos-task2-rust"
rm -rf "$build_dir"
mkdir -p "$build_dir"
linker_dir="$build_dir/linker"
mkdir -p "$linker_dir"
ln -sf "$cc" "$linker_dir/aarch64-unknown-linux-musl-ld"

build_environment=(
    "CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=$cc"
    "RUSTFLAGS=-C target-feature=+crt-static"
)
if [[ "$build_scope" == integrated ]]; then
    build_environment+=(
        "CXX_aarch64_unknown_linux_musl=$cxx"
        "AR_aarch64_unknown_linux_musl=$ar"
        "NCNN_PREFIX=$ncnn_prefix"
    )
fi
env "${build_environment[@]}" cargo build --release --target aarch64-unknown-linux-musl \
    "${cargo_feature_args[@]}" \
    --manifest-path "$app_dir/rust/Cargo.toml" --target-dir "$build_dir"
out="$build_dir/aarch64-unknown-linux-musl/release/starryos-task2-endpoint"
test -x "$out"
affinity_helper="$build_dir/task123-affinity"
"$cc" -std=c11 -O2 -Wall -Wextra -Werror -static -no-pie \
    "$app_dir/affinity_exec.c" -o "$affinity_helper"

install -Dm0755 "$out" "$overlay_dir/usr/bin/starry-udp-probe"
install -Dm0755 "$app_dir/udp-probe.sh" "$overlay_dir/usr/bin/starry-udp-probe.sh"
install -Dm0755 "$out" "$overlay_dir/usr/bin/starry-t2n1-endpoint"
install -Dm0755 "$app_dir/t2n1-run.sh" "$overlay_dir/usr/bin/t2n1-run.sh"
install -Dm0755 "$affinity_helper" "$overlay_dir/usr/bin/task123-affinity"
install -Dm0755 "$app_dir/wait-log.sh" "$overlay_dir/usr/bin/task123-wait-log"
install -Dm0755 "$app_dir/task1-topology.sh" "$overlay_dir/usr/bin/task123-topology"
install -Dm0755 "$app_dir/task1-topology.sh" "$overlay_dir/usr/bin/t1"
if [[ "$build_scope" == integrated ]]; then
    install -Dm0644 "$yolo_assets/yolo11n.ncnn.param" \
        "$overlay_dir/usr/share/task3-yolo/yolo11n.ncnn.param"
    install -Dm0644 "$yolo_assets/yolo11n.ncnn.bin" \
        "$overlay_dir/usr/share/task3-yolo/yolo11n.ncnn.bin"
    install -Dm0644 "$yolo_assets/input.ppm" \
        "$overlay_dir/usr/share/task3-yolo/input.ppm"
    install -Dm0644 "$ab_manifest" \
        "$overlay_dir/usr/share/task3-yolo/task3-ab/manifest.tsv"
    while IFS=$'\t' read -r image_id filename expected_sha256 truth_target expected_behavior; do
        [[ -z "$image_id" || "$image_id" == \#* ]] && continue
        actual_sha256="$(sha256sum "$ab_assets/$filename" | awk '{print $1}')"
        if [[ "$actual_sha256" != "$expected_sha256" ]]; then
            echo "prebuild: Task-3 A/B image hash mismatch for $image_id" >&2
            exit 1
        fi
        install -Dm0644 "$ab_assets/$filename" \
            "$overlay_dir/usr/share/task3-yolo/task3-ab/$filename"
    done < "$ab_manifest"
fi
echo "prebuild: starryos-task2 endpoint built for $arch (scope=$build_scope)"
