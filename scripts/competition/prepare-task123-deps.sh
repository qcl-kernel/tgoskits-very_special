#!/usr/bin/env bash
set -euo pipefail

# Fetch the external toolchain, sources, and model needed by the portable
# Task 1-3 QEMU paths. All state stays below this worktree.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/scripts/lib/task123-tools.sh"
cache_dir="${TASK123_DOWNLOAD_CACHE:-$repo_root/tmp/competition-task123/downloads}"
deps_dir="${TASK123_DEPS_DIR:-$repo_root/.deps/task123}"
zephyr_revision="dccb09599635bdff17633fa7e9dab014b91dce90"
ncnn_revision="946fe3fb14a8dff8c06df763f67be522167b2f00"
pnnx_revision="20260526"
toolchain_archive="$cache_dir/aarch64-linux-musl-cross.tgz"
zephyr_archive="$cache_dir/zephyr-$zephyr_revision.tar.gz"
ncnn_archive="$cache_dir/ncnn-$ncnn_revision.tar.gz"
pnnx_archive="$cache_dir/pnnx-$pnnx_revision-linux.zip"
yolo_onnx="$cache_dir/yolo11n.onnx"
toolchain_sha256="c909817856d6ceda86aa510894fa3527eac7989f0ef6e87b5721c58737a06c38"
zephyr_sha256="f4c6bc6ad9f741759ac6bebbd5e378f827fe73cee851a0c35950fd0c0da0d909"
ncnn_sha256="20f0630df2686bba9f98beb8b6e24e9fd02a0c1d7cdbb511e6bd6287c68c31e0"
pnnx_sha256="dde08ce2e2e1744a4ddce576910b451a860663af91d2aeed0d21ebee4fa69955"
yolo_onnx_sha256="634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b"

prepare_zephyr_python() {
    local zephyr_base="$1" requirements requirements_sha256
    local python_env python_bin temporary bootstrap_python
    requirements="$zephyr_base/scripts/requirements-base.txt"
    [[ -f "$requirements" ]] || {
        printf 'error: pinned Zephyr Python requirements are missing: %s\n' \
            "$requirements" >&2
        return 1
    }

    if [[ -n "${TASK123_PYTHON:-}" ]]; then
        python_bin="$(resolve_task123_python "$repo_root" "$zephyr_revision")" || return
        task123_check_zephyr_python "$python_bin"
        printf 'reuse configured Zephyr Python: %s\n' "$python_bin"
        return
    fi

    python_env="$deps_dir/zephyr-python-$zephyr_revision"
    python_bin="$python_env/bin/python3"
    requirements_sha256="$(sha256sum "$requirements" | awk '{print $1}')"
    if [[ "$(cat "$python_env/.task123-requirements-sha256" 2>/dev/null || true)" == \
        "$requirements_sha256" ]] && task123_check_zephyr_python "$python_bin" &&
        "$python_bin" -m pip check >/dev/null; then
        printf 'reuse verified Zephyr Python: %s\n' "$python_env"
        return
    fi

    bootstrap_python="$(command -v python3 2>/dev/null || true)"
    [[ -n "$bootstrap_python" ]] || {
        printf 'error: python3 is required to prepare the Zephyr environment\n' >&2
        return 1
    }
    temporary="$(mktemp -d "$deps_dir/.zephyr-python-$zephyr_revision.XXXXXXXX")"
    if ! "$bootstrap_python" -m venv "$temporary"; then
        rm -rf -- "$temporary"
        printf 'error: failed to create a Python virtual environment; install python3-venv\n' >&2
        return 1
    fi
    if ! "$temporary/bin/python3" -m pip install --requirement "$requirements" ||
        ! "$temporary/bin/python3" -m pip check ||
        ! task123_check_zephyr_python "$temporary/bin/python3"; then
        rm -rf -- "$temporary"
        printf 'error: failed to install the pinned Zephyr Python requirements\n' >&2
        return 1
    fi
    printf '%s\n' "$requirements_sha256" > "$temporary/.task123-requirements-sha256"
    "$temporary/bin/python3" --version > "$temporary/.task123-python-version" 2>&1
    "$temporary/bin/python3" -m pip freeze --all > "$temporary/pip-freeze.txt"

    case "$python_env" in
        "$deps_dir"/zephyr-python-"$zephyr_revision") ;;
        *)
            rm -rf -- "$temporary"
            printf 'error: refusing to replace unexpected Python environment: %s\n' \
                "$python_env" >&2
            return 1
            ;;
    esac
    rm -rf -- "$python_env"
    mv -- "$temporary" "$python_env"
    printf 'prepared Zephyr Python: %s\n' "$python_env"
}

write_tree_manifest() {
    local destination="$1"
    local manifest="$destination/.task123-tree-sha256"
    local temporary="$manifest.tmp.$$"

    (
        cd "$destination"
        find . -type f ! -name '.task123-tree-sha256' ! -name '.task123-tree-sha256.tmp.*' \
            -print0 | LC_ALL=C sort -z | while IFS= read -r -d '' path; do
                sha256sum "$path"
            done
    ) > "$temporary"
    mv -f -- "$temporary" "$manifest"
}

download_verified() {
    local url="$1" destination="$2" expected_sha256="$3" temporary
    if [[ -f "$destination" ]] &&
        [[ "$(sha256sum "$destination" | awk '{print $1}')" == "$expected_sha256" ]]; then
        printf 'reuse verified download: %s\n' "$destination"
        return
    fi
    temporary="$destination.download.$$"
    rm -f -- "$temporary"
    curl --fail --location --retry 3 --output "$temporary" "$url"
    printf '%s  %s\n' "$expected_sha256" "$temporary" | sha256sum -c -
    mv -f -- "$temporary" "$destination"
}

extract_verified() {
    local archive="$1" expected_sha256="$2" destination="$3" marker="$4"
    if [[ -f "$destination/.task123-source-sha256" ]] &&
        [[ "$(cat "$destination/.task123-source-sha256")" == "$expected_sha256" ]] &&
        task123_source_is_pristine "$destination"; then
        printf 'reuse verified dependency: %s\n' "$destination"
        return
    fi
    if [[ -e "$destination" ]]; then
        printf 'error: dependency directory failed integrity verification: %s\n' \
            "$destination" >&2
        printf 'error: preserve or move that directory, then rerun prepare\n' >&2
        return 1
    fi
    tar -xzf "$archive" -C "$deps_dir"
    [[ -d "$destination" ]] || {
        printf 'error: archive did not create expected directory: %s\n' "$destination" >&2
        return 1
    }
    printf '%s\n' "$expected_sha256" > "$destination/.task123-source-sha256"
    if [[ -n "$marker" ]]; then
        printf '%s\n' "$marker" > "$destination/.task123-source-revision"
    fi
    write_tree_manifest "$destination"
}

extract_pnnx_verified() {
    local archive="$1" expected_sha256="$2" destination="$3"
    if [[ "$(cat "$destination/.task123-source-sha256" 2>/dev/null || true)" == \
        "$expected_sha256" ]] && [[ -x "$destination/pnnx" ]]; then
        printf 'reuse verified dependency: %s\n' "$destination"
        return
    fi
    if [[ -e "$destination" ]]; then
        printf 'error: dependency directory failed integrity verification: %s\n' \
            "$destination" >&2
        printf 'error: preserve or move that directory, then rerun prepare\n' >&2
        return 1
    fi
    python3 - "$archive" "$deps_dir" <<'PY'
import sys
import zipfile
from pathlib import Path, PurePosixPath

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with zipfile.ZipFile(archive) as source:
    for member in source.infolist():
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe path in {archive}: {member.filename}")
    source.extractall(destination)
PY
    [[ -f "$destination/pnnx" ]] || {
        printf 'error: pnnx archive did not create expected executable: %s\n' \
            "$destination/pnnx" >&2
        return 1
    }
    chmod 0755 "$destination/pnnx"
    printf '%s\n' "$expected_sha256" > "$destination/.task123-source-sha256"
}

command -v curl >/dev/null 2>&1 || {
    printf 'error: curl is required to download Task 1 dependencies\n' >&2
    exit 1
}
mkdir -p "$cache_dir" "$deps_dir"

if [[ "$(cat "$deps_dir/aarch64-linux-musl-cross/.task123-source-sha256" 2>/dev/null || true)" != \
    "$toolchain_sha256" ]] ||
    ! task123_source_is_pristine "$deps_dir/aarch64-linux-musl-cross" >/dev/null 2>&1; then
    download_verified \
        "https://musl.cc/aarch64-linux-musl-cross.tgz" \
        "$toolchain_archive" "$toolchain_sha256"
    extract_verified "$toolchain_archive" "$toolchain_sha256" \
        "$deps_dir/aarch64-linux-musl-cross" ""
else
    printf 'reuse verified dependency: %s\n' "$deps_dir/aarch64-linux-musl-cross"
fi
if [[ "$(cat "$deps_dir/zephyr-$zephyr_revision/.task123-source-sha256" 2>/dev/null || true)" != \
    "$zephyr_sha256" ]] ||
    ! task123_source_is_pristine "$deps_dir/zephyr-$zephyr_revision" >/dev/null 2>&1; then
    download_verified \
        "https://github.com/zephyrproject-rtos/zephyr/archive/$zephyr_revision.tar.gz" \
        "$zephyr_archive" "$zephyr_sha256"
    extract_verified "$zephyr_archive" "$zephyr_sha256" \
        "$deps_dir/zephyr-$zephyr_revision" "$zephyr_revision"
else
    printf 'reuse verified dependency: %s\n' "$deps_dir/zephyr-$zephyr_revision"
fi
if [[ "$(cat "$deps_dir/ncnn-$ncnn_revision/.task123-source-sha256" 2>/dev/null || true)" != \
    "$ncnn_sha256" ]] ||
    ! task123_source_is_pristine "$deps_dir/ncnn-$ncnn_revision" >/dev/null 2>&1; then
    download_verified \
        "https://github.com/Tencent/ncnn/archive/$ncnn_revision.tar.gz" \
        "$ncnn_archive" "$ncnn_sha256"
    extract_verified "$ncnn_archive" "$ncnn_sha256" \
        "$deps_dir/ncnn-$ncnn_revision" "$ncnn_revision"
else
    printf 'reuse verified dependency: %s\n' "$deps_dir/ncnn-$ncnn_revision"
fi
download_verified \
    "https://github.com/pnnx/pnnx/releases/download/$pnnx_revision/pnnx-$pnnx_revision-linux.zip" \
    "$pnnx_archive" "$pnnx_sha256"
extract_pnnx_verified "$pnnx_archive" "$pnnx_sha256" \
    "$deps_dir/pnnx-$pnnx_revision-linux"
download_verified \
    "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx" \
    "$yolo_onnx" "$yolo_onnx_sha256"

test -x "$deps_dir/aarch64-linux-musl-cross/bin/aarch64-linux-musl-gcc"
test -f "$deps_dir/zephyr-$zephyr_revision/CMakeLists.txt"
test -f "$deps_dir/ncnn-$ncnn_revision/CMakeLists.txt"
test -x "$deps_dir/pnnx-$pnnx_revision-linux/pnnx"
test -s "$yolo_onnx"
prepare_zephyr_python "$deps_dir/zephyr-$zephyr_revision"
printf 'TASK123_DEPS_PASS\n'
printf 'CROSS_ROOT=%s\n' "$deps_dir/aarch64-linux-musl-cross"
printf 'ZEPHYR_BASE=%s\n' "$deps_dir/zephyr-$zephyr_revision"
printf 'NCNN_SOURCE=%s\n' "$deps_dir/ncnn-$ncnn_revision"
printf 'PNNX=%s\n' "$deps_dir/pnnx-$pnnx_revision-linux/pnnx"
printf 'YOLO_ONNX=%s\n' "$yolo_onnx"
printf 'TASK123_PYTHON=%s\n' \
    "$(resolve_task123_python "$repo_root" "$zephyr_revision")"
