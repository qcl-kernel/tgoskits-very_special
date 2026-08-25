#!/bin/sh
set -eu

exec </dev/ttyS0 >/dev/ttyS0 2>&1
payload_root="${G2_PAYLOAD_ROOT:-}"
busybox="${payload_root}/bin/busybox"

echo TASK1_IDLE_READY workload=none
exec "$busybox" sleep 600
