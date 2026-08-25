#!/usr/bin/env bash
# Build idle and hybrid-pressure Task 1 payloads from verified inputs.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
base_cpio="${1:-}"
expected_sha256="${2:-}"
communication_binary="${3:-}"
communication_sha256="${4:-}"
output_dir="${5:-}"
work_root=""

main() {
    parse_arguments "$@"
    require_tools
    verify_base_payload
    verify_communication_binary
    prepare_output
    unpack_base_payload
    build_payload idle "$repo_root/scripts/board/task1-starry-idle-init.sh"
    build_payload pressure "$repo_root/scripts/board/task1-starry-rknn-pressure-init.sh"
    write_manifest
    printf 'Task 1 StarryOS payloads: %s\n' "$output_dir"
}

parse_arguments() {
    if [[ $# -ne 5 ]]; then
        printf 'usage: %s BASE_RKNN_CPIO BASE_SHA256 TASK2_BINARY TASK2_SHA256 NEW_OUTPUT_DIRECTORY\n' \
            "${BASH_SOURCE[0]##*/}" >&2
        exit 2
    fi
    base_cpio="$(realpath "$base_cpio")"
    communication_binary="$(realpath "$communication_binary")"
    output_dir="$(realpath -m "$output_dir")"
    if [[ ! "$expected_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
        printf 'error: EXPECTED_SHA256 must contain exactly 64 hexadecimal digits\n' >&2
        exit 2
    fi
    expected_sha256="${expected_sha256,,}"
    if [[ ! "$communication_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
        printf 'error: TASK2_SHA256 must contain exactly 64 hexadecimal digits\n' >&2
        exit 2
    fi
    communication_sha256="${communication_sha256,,}"
}

require_tools() {
    local executable
    for executable in cpio find install sha256sum; do
        if ! command -v "$executable" >/dev/null 2>&1; then
            printf 'error: required executable is unavailable: %s\n' "$executable" >&2
            exit 1
        fi
    done
}

verify_base_payload() {
    if [[ ! -f "$base_cpio" ]]; then
        printf 'error: base RKNN cpio does not exist: %s\n' "$base_cpio" >&2
        exit 1
    fi
    printf '%s  %s\n' "$expected_sha256" "$base_cpio" | sha256sum -c -
}

verify_communication_binary() {
    if [[ ! -f "$communication_binary" ]]; then
        printf 'error: Task 1 communication binary does not exist: %s\n' \
            "$communication_binary" >&2
        exit 1
    fi
    printf '%s  %s\n' "$communication_sha256" "$communication_binary" | sha256sum -c -
}

prepare_output() {
    if [[ -e "$output_dir" ]] &&
        [[ -n "$(find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
        printf 'error: output directory must be absent or empty: %s\n' "$output_dir" >&2
        exit 2
    fi
    mkdir -p "$output_dir"
    work_root="$(mktemp -d "${TMPDIR:-/tmp}/task1-starry-payloads.XXXXXX")"
    trap release_work_root EXIT
}

release_work_root() {
    if [[ -n "$work_root" && -d "$work_root" ]]; then
        rm -rf -- "$work_root"
    fi
}

unpack_base_payload() {
    mkdir -p "$work_root/base"
    (cd "$work_root/base" && cpio -id --quiet < "$base_cpio")
    local required
    for required in \
        bin/busybox \
        rknn/rknn_yolov8_bench \
        rknn/lib/ld-linux-aarch64.so.1 \
        rknn/lib/librknnrt.so \
        rknn/model/yolov8.rknn \
        rknn/validation/scene-images.txt; do
        if [[ ! -f "$work_root/base/$required" ]]; then
            printf 'error: verified base payload is missing: %s\n' "$required" >&2
            exit 1
        fi
    done
}

build_payload() {
    local mode="$1" init_source="$2" root
    root="$work_root/$mode"
    mkdir -p "$root"
    cp -a "$work_root/base/." "$root/"
    if [[ "$mode" == pressure ]]; then
        install -m 0755 "$communication_binary" "$root/bin/task2-net"
    fi
    install -m 0755 "$init_source" "$root/init"
    (cd "$root" && find . -print0 | sort -z | cpio --null -o -H newc --quiet) \
        > "$output_dir/starry-task1-$mode.cpio"
}

write_manifest() {
    printf '%s  %s\n' "$expected_sha256" "$base_cpio" > "$output_dir/BASE_INPUT_SHA256.txt"
    printf '%s  %s\n' "$communication_sha256" "$communication_binary" \
        > "$output_dir/TASK2_INPUT_SHA256.txt"
    (
        cd "$output_dir"
        sha256sum starry-task1-idle.cpio starry-task1-pressure.cpio > SHA256SUMS.txt
    )
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
