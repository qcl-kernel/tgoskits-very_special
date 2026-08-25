#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
busybox="${BUSYBOX_STATIC:-}"
controller="${TASK2_BINARY:-}"
output="${1:-}"

if [[ -z "$output" || ! -f "$busybox" || ! -f "$controller" ]]; then
    printf 'usage: BUSYBOX_STATIC=... TASK2_BINARY=... %s OUTPUT.cpio\n' \
        "${BASH_SOURCE[0]##*/}" >&2
    exit 2
fi
if ! grep -aFq 'TASK2_BENCHMARK_BEGIN transactions=' "$controller"; then
    printf 'error: controller does not contain the bounded benchmark marker: %s\n' \
        "$controller" >&2
    exit 1
fi

root_dir="$(mktemp -d "${TMPDIR:-/tmp}/task2-benchmark-payload.XXXXXX")"
trap 'rm -rf -- "$root_dir"' EXIT
install -D -m 0755 "$busybox" "$root_dir/bin/busybox"
install -D -m 0755 "$controller" "$root_dir/bin/task2-net"
install -m 0755 "$repo_root/scripts/task2/benchmark-init.sh" "$root_dir/init"
mkdir -p "$(dirname "$output")"
(cd "$root_dir" && find . -print0 | sort -z | cpio --null -o -H newc --quiet) > "$output"
sha256sum "$output" > "$output.sha256"
printf 'payload=%s\nsha256=%s\n' \
    "$(realpath "$output")" "$(awk '{print $1}' "$output.sha256")"
