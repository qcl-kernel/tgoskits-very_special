#!/usr/bin/env bash
# Build fresh fixed-perception and RKNN Task 3 physical-board FIT matrices.
# This is a host-only builder; it never opens the board or writes storage.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
output_root=""
busybox="${BUSYBOX_STATIC:-}"
rknn_bundle="${RKNN_BUNDLE:-}"
host_dtb="${ATK_HOST_DTB:-${TASK123_BOARD_DTB:-}}"

main() {
    parse_arguments "$@"
    require_inputs
    build_arm fixed fixed-perception
    build_arm rknn rknn
    write_input_manifest
    printf 'TASK3_MATRIX_BUILD_PASS output=%s\n' "$output_root"
}

parse_arguments() {
    if [[ $# -ne 1 ]]; then
        printf 'usage: BUSYBOX_STATIC=... RKNN_BUNDLE=... %s <new-output-directory>\n' \
            "${BASH_SOURCE[0]##*/}" >&2
        exit 2
    fi
    output_root="$(realpath -m "$1")"
    if [[ -e "$output_root" ]] &&
        [[ -n "$(find "$output_root" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'error: output directory must be absent or empty: %s\n' "$output_root" >&2
        exit 2
    fi
}

require_inputs() {
    local relative executable
    [[ -f "$busybox" ]] || {
        printf 'error: BUSYBOX_STATIC must name a static AArch64 BusyBox\n' >&2
        exit 1
    }
    [[ -d "$rknn_bundle" ]] || {
        printf 'error: RKNN_BUNDLE must name the external RKNN runtime/model/input directory\n' >&2
        exit 1
    }
    [[ -f "$host_dtb" ]] || {
        printf 'error: set ATK_HOST_DTB or TASK123_BOARD_DTB to the ATK-DLRK3588 host DTB\n' >&2
        exit 1
    }
    for relative in \
        rknn_yolov8_bench \
        lib/ld-linux-aarch64.so.1 \
        lib/libc.so.6 \
        lib/libm.so.6 \
        lib/libstdc++.so.6 \
        lib/librknnrt.so \
        model/yolov8.rknn \
        model/coco_80_labels_list.txt; do
        [[ -f "$rknn_bundle/$relative" ]] || {
            printf 'error: RKNN bundle asset is missing: %s\n' "$relative" >&2
            exit 1
        }
    done
    for executable in cargo cpio file sha256sum; do
        command -v "$executable" >/dev/null 2>&1 || {
            printf 'error: required executable is unavailable: %s\n' "$executable" >&2
            exit 1
        }
    done
    busybox="$(realpath "$busybox")"
    rknn_bundle="$(realpath "$rknn_bundle")"
    host_dtb="$(realpath "$host_dtb")"
    export ATK_HOST_DTB="$host_dtb"
    mkdir -p "$output_root"
}

build_arm() {
    local mode="$1" model="$2"
    local arm_root="$output_root/$mode"
    local controller_root="$arm_root/controller"
    local payload="$arm_root/payload-$mode.cpio"
    local artifact_root="$arm_root/artifacts"
    mkdir -p "$arm_root/logs"
    printf 'building Task 3 %s controller\n' "$mode"

    TASK3_CONTROL_LOOP=1 \
    TASK3_MODEL="$model" \
    TASK3_MODEL_PATH="${TASK3_MODEL_PATH:-/usr/share/task3-yolo}" \
    TASK3_RKNN_CONTROL_PATH="${TASK3_RKNN_CONTROL_PATH:-/rknn-control.txt}" \
    TASK3_RKNN_ACK_PATH="${TASK3_RKNN_ACK_PATH:-/rknn-control.ack}" \
    OUT_DIR="$controller_root" \
        "$repo_root/scripts/test/net-dual-guest/build-linux-task2.sh" \
        >"$arm_root/logs/build-controller.log" 2>&1

    if [[ "$mode" == rknn ]]; then
        BUSYBOX_STATIC="$busybox" \
        TASK2_BINARY="$controller_root/controller/task2-net" \
        RKNN_BUNDLE="$rknn_bundle" \
            "$repo_root/scripts/task3/build-hybrid-scene-payload.sh" \
            rknn "$payload" >"$arm_root/logs/build-payload.log" 2>&1
    else
        BUSYBOX_STATIC="$busybox" \
        TASK2_BINARY="$controller_root/controller/task2-net" \
            "$repo_root/scripts/task3/build-hybrid-scene-payload.sh" \
            fixed "$payload" >"$arm_root/logs/build-payload.log" 2>&1
    fi

    printf 'building Task 3 %s RR/FP-RR board FITs\n' "$mode"
    STARRY_INITRD="$payload" \
    ATK_TASK1_TOPOLOGY="${ATK_TASK1_TOPOLOGY:-communication-share}" \
    TASK1_ZEPHYR_SAMPLE_COUNT="${TASK1_ZEPHYR_SAMPLE_COUNT:-300}" \
    TASK1_ZEPHYR_DUMP_CHUNK_ROWS="${TASK1_ZEPHYR_DUMP_CHUNK_ROWS:-256}" \
        "$repo_root/scripts/board/build-atk-zephyr-task123-unified.sh" \
        "$artifact_root" >"$arm_root/logs/build-board.log" 2>&1

    (
        cd "$arm_root"
        find . -type f ! -path './artifacts/build/*' ! -name SHA256SUMS.txt -print0 \
            | sort -z | xargs -0 sha256sum > SHA256SUMS.txt
    )
}

write_input_manifest() {
    {
        printf '# External inputs hashed before Task 3 matrix construction\n'
        sha256sum "$host_dtb"
        sha256sum "$busybox"
        find "$rknn_bundle" -type f -print0 | sort -z | xargs -0 sha256sum
    } > "$output_root/INPUT-SHA256SUMS.txt"
    {
        printf 'source_commit=%s\n' "$(git -C "$repo_root" rev-parse HEAD)"
        printf 'busybox=%s\n' "$busybox"
        printf 'rknn_bundle=%s\n' "$rknn_bundle"
        printf 'host_dtb=%s\n' "$host_dtb"
        printf 'topology=%s\n' "${ATK_TASK1_TOPOLOGY:-communication-share}"
        printf 'sample_count=%s\n' "${TASK1_ZEPHYR_SAMPLE_COUNT:-300}"
    } > "$output_root/build.manifest"
}

main "$@"
