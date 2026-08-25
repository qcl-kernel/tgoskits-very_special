#!/usr/bin/env bash

# Resolve external Task 1-3 tools without assuming a developer username or
# home-directory layout. Callers may set the named override; otherwise the
# executable must be discoverable through PATH.
task123_source_revision() {
    local source="$1"

    if [[ -e "$source/.git" ]]; then
        git -C "$source" rev-parse HEAD
        return
    fi
    if [[ -f "$source/.task123-source-revision" ]]; then
        cat "$source/.task123-source-revision"
        return
    fi
    printf 'error: source revision is unavailable: %s\n' "$source" >&2
    return 1
}

discover_task123_cross_root() {
    if [[ -n "${CROSS_ROOT:-}" ]]; then
        printf '%s\n' "$CROSS_ROOT"
    fi
}

resolve_task123_tool() {
    local override_name="$1"
    local executable_name="$2"
    local configured_path="${!override_name:-}"

    if [[ -n "$configured_path" ]]; then
        if [[ ! -x "$configured_path" ]]; then
            printf 'error: %s is not executable: %s\n' \
                "$override_name" "$configured_path" >&2
            return 1
        fi
        realpath "$configured_path"
        return
    fi

    local cross_root
    cross_root="$(discover_task123_cross_root)"
    if [[ -n "$cross_root" && -x "$cross_root/bin/$executable_name" ]]; then
        realpath "$cross_root/bin/$executable_name"
        return
    fi

    local discovered_path
    discovered_path="$(command -v "$executable_name" 2>/dev/null || true)"
    if [[ -z "$discovered_path" ]]; then
        printf 'error: required tool %s was not found; set %s or add it to PATH\n' \
            "$executable_name" "$override_name" >&2
        return 1
    fi
    realpath "$discovered_path"
}

resolve_task123_cross_prefix() {
    if [[ -n "${CROSS_COMPILE:-}" ]]; then
        if [[ ! -x "${CROSS_COMPILE}gcc" ]]; then
            printf 'error: CROSS_COMPILE gcc is not executable: %s\n' \
                "${CROSS_COMPILE}gcc" >&2
            return 1
        fi
        printf '%s\n' "$CROSS_COMPILE"
        return
    fi

    local cross_cc
    cross_cc="$(resolve_task123_tool CROSS_CC aarch64-linux-musl-gcc)" || return
    printf '%s\n' "${cross_cc%gcc}"
}

# Serialize memory-heavy QEMU runs started by the Task 1-3 entrypoints. The
# default is scoped to this worktree, while CI or multi-worktree hosts can set
# TASK123_QEMU_LOCK_FILE to a shared path. Keeping the descriptor open makes
# the kernel release the slot automatically on normal exit, error, or signal.
acquire_task123_qemu_slot() {
    local repo_root="$1"
    local lock_file="${TASK123_QEMU_LOCK_FILE:-$repo_root/tmp/competition-task123/qemu.lock}"
    local timeout_sec="${TASK123_QEMU_LOCK_TIMEOUT_SEC:-7200}"

    if [[ -n "${TASK123_QEMU_LOCK_FD:-}" ]]; then
        return 0
    fi
    if [[ ! "$timeout_sec" =~ ^[1-9][0-9]*$ ]]; then
        printf 'error: TASK123_QEMU_LOCK_TIMEOUT_SEC must be a positive integer\n' >&2
        return 2
    fi
    command -v flock >/dev/null 2>&1 || {
        printf 'error: flock is required to coordinate the Task 1-3 QEMU execution slot\n' >&2
        return 1
    }

    lock_file="$(realpath -m "$lock_file")"
    mkdir -p "$(dirname "$lock_file")"
    exec {TASK123_QEMU_LOCK_FD}>"$lock_file"
    if ! flock -w "$timeout_sec" "$TASK123_QEMU_LOCK_FD"; then
        printf 'error: timed out waiting %s seconds for the Task 1-3 QEMU execution slot: %s\n' \
            "$timeout_sec" "$lock_file" >&2
        exec {TASK123_QEMU_LOCK_FD}>&-
        unset TASK123_QEMU_LOCK_FD
        return 1
    fi
    TASK123_QEMU_LOCK_PATH="$lock_file"
}

# Create a short, uniquely owned directory for QMP, serial, and disposable
# runtime artifacts. Unix-domain socket paths are length-limited, so the
# system temporary directory is preferable to a potentially deep workspace.
create_task123_runtime_dir() {
    local runtime_parent="${TASK123_RUNTIME_PARENT:-${TMPDIR:-/tmp}}"
    local runtime_dir

    mkdir -p "$runtime_parent"
    runtime_dir="$(mktemp -d "$runtime_parent/tgoskits-task123.XXXXXXXX")" || return
    printf '%s\n' "$$" > "$runtime_dir/.task123-owned"
    printf '%s\n' "$runtime_dir"
}

# Remove only a directory created by create_task123_runtime_dir. The marker
# prevents a malformed or externally supplied path from widening cleanup.
remove_task123_runtime_dir() {
    local runtime_dir="$1"
    local marker="$runtime_dir/.task123-owned"

    case "${runtime_dir##*/}" in
        tgoskits-task123.*) ;;
        *)
            printf 'error: refusing to remove unrecognized Task 1-3 runtime directory: %s\n' \
                "$runtime_dir" >&2
            return 1
            ;;
    esac
    if [[ ! -f "$marker" ]]; then
        printf 'error: refusing to remove unowned Task 1-3 runtime directory: %s\n' \
            "$runtime_dir" >&2
        return 1
    fi
    if [[ "$(cat "$marker")" != "$$" ]]; then
        printf 'error: refusing to remove Task 1-3 runtime directory owned by another shell: %s\n' \
            "$runtime_dir" >&2
        return 1
    fi
    rm -rf -- "$runtime_dir"
}

# Pick one or more currently free loopback TCP ports. Callers should launch
# the owning process immediately because the kernel reservation ends when this
# helper exits.
allocate_task123_tcp_ports() {
    local count="${1:-1}"

    [[ "$count" =~ ^[1-9][0-9]*$ ]] || {
        printf 'error: TCP port count must be a positive integer: %s\n' "$count" >&2
        return 2
    }
    python3 - "$count" <<'PY'
import socket
import sys

sockets = []
for _ in range(int(sys.argv[1])):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    sockets.append(listener)
for listener in sockets:
    print(listener.getsockname()[1])
PY
}

# Record enough source identity to distinguish clean commit evidence from an
# explicitly allowed worktree experiment.
write_task123_source_identity() {
    local repo_root="$1"
    local output_dir="$2"
    local patch_path="$output_dir/worktree.patch"
    local untracked_path="$output_dir/untracked-files.txt"
    local status_path="$output_dir/git-status.txt"

    mkdir -p "$output_dir"
    git -C "$repo_root" rev-parse HEAD > "$output_dir/git-head.txt"
    git -C "$repo_root" status --porcelain=v1 > "$status_path"
    git -C "$repo_root" diff --binary HEAD > "$patch_path"
    : > "$untracked_path"
    while IFS= read -r -d '' path; do
        printf '%s  %s\n' "$(sha256sum "$repo_root/$path" | awk '{print $1}')" "$path" \
            >> "$untracked_path"
    done < <(git -C "$repo_root" ls-files --others --exclude-standard -z)
    {
        printf 'git_head=%s\n' "$(cat "$output_dir/git-head.txt")"
        if [[ -s "$status_path" ]]; then
            printf 'worktree=dirty\n'
        else
            printf 'worktree=clean\n'
        fi
        printf 'tracked_patch_sha256=%s\n' "$(sha256sum "$patch_path" | awk '{print $1}')"
        printf 'untracked_manifest_sha256=%s\n' \
            "$(sha256sum "$untracked_path" | awk '{print $1}')"
    } > "$output_dir/source-identity.txt"
}
