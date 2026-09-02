#!/bin/sh
set -u

timeout="${1:-}"
needle="${2:-}"
log="${3:-}"

case "$timeout" in
    ''|*[!0-9]*|0)
        echo "task123-wait-log: timeout must be a positive integer" >&2
        exit 2
        ;;
esac
if [ -z "$needle" ] || [ -z "$log" ]; then
    echo "usage: task123-wait-log TIMEOUT NEEDLE LOG" >&2
    exit 2
fi

elapsed=0
while [ "$elapsed" -lt "$timeout" ]; do
    if grep -F -m1 -- "$needle" "$log" 2>/dev/null; then
        exit 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

echo "task123-wait-log: timed out waiting for '$needle' in $log" >&2
exit 1
