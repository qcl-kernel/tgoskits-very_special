#!/bin/sh
set -u

exec </dev/ttyS0 >/dev/ttyS0 2>&1
payload_root="${G2_PAYLOAD_ROOT:-}"
busybox="${payload_root}/bin/busybox"

echo TASK2_BENCHMARK_PAYLOAD_READY cpu=1
"$busybox" ip addr add 10.0.42.15/24 dev eth0
"$busybox" ip link set eth0 up
"$busybox" taskset -c 1 "${payload_root}/bin/task2-net"
controller_rc=$?
echo TASK2_BENCHMARK_PROCESS_EXIT rc="$controller_rc"
exec "$busybox" sleep 300
