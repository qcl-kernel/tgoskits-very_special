#!/bin/sh
set -u
exec </dev/ttyS0 >/dev/ttyS0 2>&1
payload_root="${G2_PAYLOAD_ROOT:-}"
busybox="${payload_root}/bin/busybox"
echo TASK3_HYBRID_SCENE_BEGIN source=fixed-perception communication_cpu=1 ai_cpu=0
"$busybox" ip addr add 10.0.42.15/24 dev eth0
"$busybox" taskset -c 1 "${payload_root}/bin/task2-net" \
    >/tmp/task3-controller.log 2>&1
controller_rc=$?
"$busybox" cat /tmp/task3-controller.log
echo TASK3_HYBRID_SCENE_END source=fixed-perception controller_rc="$controller_rc" producer_rc=na
exec "$busybox" sleep 300
