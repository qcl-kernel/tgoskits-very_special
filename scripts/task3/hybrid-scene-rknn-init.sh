#!/bin/sh
set -u
exec </dev/ttyS0 >/dev/ttyS0 2>&1
payload_root="${G2_PAYLOAD_ROOT:-}"
busybox="${payload_root}/bin/busybox"
echo TASK3_HYBRID_SCENE_BEGIN source=rknn communication_cpu=1 ai_cpu=0
"$busybox" rm -f /rknn-control.txt /rknn-control.txt.tmp \
    /rknn-control.ack /rknn-control.ack.tmp \
    /tmp/scene-expected.txt /tmp/task3-controller.log /tmp/rknn-scene-profile.log
"$busybox" ip addr add 10.0.42.15/24 dev eth0
"$busybox" taskset -c 1 "${payload_root}/bin/task2-net" \
    >/tmp/task3-controller.log 2>&1 &
control_pid=$!
echo TASK3_CONTROLLER_STARTED cpu=1 pid="$control_pid"
cd "${payload_root}/rknn" || exit 100
export LD_LIBRARY_PATH="${payload_root}/rknn/lib"
"$busybox" taskset -c 0 \
    "${payload_root}/rknn/lib/ld-linux-aarch64.so.1" \
    --library-path "${payload_root}/rknn/lib" \
    "${payload_root}/rknn/rknn_yolov8_bench" \
    --validate-list validation/scene-images.txt \
    --write-expected /tmp/scene-expected.txt \
    --control-output /rknn-control.txt \
    --control-ack /rknn-control.ack \
    --min-confidence 25 \
    --core-mask all \
    --profile --profile-frames \
    >/tmp/rknn-scene-profile.log 2>&1
producer_rc=$?
"$busybox" sleep 1
"$busybox" cat /tmp/task3-controller.log
"$busybox" cat /tmp/rknn-scene-profile.log
if [ "$producer_rc" -eq 0 ]; then
    echo TASK3_HYBRID_SCENE_END source=rknn controller_complete=1 producer_rc=0
else
    echo TASK3_HYBRID_SCENE_END source=rknn controller_complete=0 producer_rc="$producer_rc"
fi
exec "$busybox" sleep 300
