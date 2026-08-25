#!/bin/sh
set -u

exec </dev/ttyS0 >/dev/ttyS0 2>&1
payload_root="${G2_PAYLOAD_ROOT:-}"
busybox="${payload_root}/bin/busybox"
communication_attempt=0
max_communication_attempts=5
controller_pid=""
controller_ready=0
validation_loops="${TASK1_PRESSURE_VALIDATION_LOOPS:-1000000}"

"$busybox" ip addr add 10.0.42.15/24 dev eth0
"$busybox" ip link set eth0 up

echo TASK1_COMMUNICATION_START cpu=1 mode=continuous-control
while [ "$communication_attempt" -lt "$max_communication_attempts" ]; do
    communication_attempt=$((communication_attempt + 1))
    echo TASK1_COMMUNICATION_ATTEMPT attempt="$communication_attempt" cpu=1
    "$busybox" taskset -c 1 "${payload_root}/bin/task2-net" &
    controller_pid=$!
    "$busybox" sleep 1
    if "$busybox" kill -0 "$controller_pid" 2>/dev/null; then
        controller_ready=1
        break
    fi
    echo TASK1_COMMUNICATION_RETRY attempt="$communication_attempt" reason=exited delay_ms=1000
    "$busybox" sleep 1
done

if [ "$controller_ready" -ne 1 ]; then
    echo TASK1_COMMUNICATION_FAILED attempts="$communication_attempt"
    exit 101
fi
echo TASK1_COMMUNICATION_PROCESS_READY cpu=1 pid="$controller_pid" attempt="$communication_attempt"

cd "${payload_root}/rknn" || exit 100
export LD_LIBRARY_PATH="${payload_root}/rknn/lib"
"$busybox" rm -f /tmp/task1-scene-expected.txt
echo TASK1_PRESSURE_READY workload=rknn-yolov8 cpu=0

echo TASK1_PRESSURE_SEED_BEGIN images=11
"$busybox" taskset -c 0 \
    "${payload_root}/rknn/lib/ld-linux-aarch64.so.1" \
    --library-path "${payload_root}/rknn/lib" \
    "${payload_root}/rknn/rknn_yolov8_bench" \
    --validate-list validation/scene-images.txt \
    --write-expected /tmp/task1-scene-expected.txt \
    --min-confidence 25 \
    --core-mask all \
    --profile \
    >/tmp/task1-rknn-seed.log 2>&1
rc=$?
echo TASK1_PRESSURE_SEED_COMPLETE images=11 rc="$rc"
if [ "$rc" -ne 0 ]; then
    "$busybox" cat /tmp/task1-rknn-seed.log
    exit "$rc"
fi

echo TASK1_PRESSURE_SUSTAINED_BEGIN loops="$validation_loops" images_per_loop=11
"$busybox" taskset -c 0 \
    "${payload_root}/rknn/lib/ld-linux-aarch64.so.1" \
    --library-path "${payload_root}/rknn/lib" \
    "${payload_root}/rknn/rknn_yolov8_bench" \
    --validate-list validation/scene-images.txt \
    --expected /tmp/task1-scene-expected.txt \
    --validation-loops "$validation_loops" \
    --min-confidence 25 \
    --core-mask all \
    --profile --profile-frames \
    >/tmp/task1-rknn-profile.log 2>&1 &
pressure_pid=$!
pressure_progress=0
while "$busybox" kill -0 "$pressure_pid" 2>/dev/null; do
    pressure_progress=$((pressure_progress + 1))
    echo TASK1_PRESSURE_PROGRESS sample="$pressure_progress" pid="$pressure_pid" alive=1
    "$busybox" sleep 1
done
wait "$pressure_pid"
rc=$?
echo TASK1_PRESSURE_SUSTAINED_END loops="$validation_loops" rc="$rc"
if [ "$rc" -ne 0 ]; then
    "$busybox" cat /tmp/task1-rknn-profile.log
fi
exit "$rc"
