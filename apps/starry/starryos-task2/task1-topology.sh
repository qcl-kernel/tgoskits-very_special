#!/bin/sh
set -eu

ai_log=/tmp/task1-ai.log
ai_pid_file=/tmp/task1-ai.pid
communication_log=/tmp/task1-communication.log
communication_pid_file=/tmp/task1-communication.pid

start_communication() {
    rm -f "$communication_log" "$communication_pid_file"
    /usr/bin/task123-affinity 1 /bin/sh /usr/bin/t2n1-run.sh task2 \
        >"$communication_log" 2>&1 &
    pid=$!
    echo "$pid" >"$communication_pid_file"
    echo "TASK1_TOPOLOGY_COMMUNICATION_STARTED cpu=1 pid=$pid"
}

start_ai() {
    rm -f "$ai_log" "$ai_pid_file"
    /usr/bin/task123-affinity 0 /bin/sh -c \
        'while :; do /bin/sh /usr/bin/t2n1-run.sh model-only; done' \
        >"$ai_log" 2>&1 &
    pid=$!
    echo "$pid" >"$ai_pid_file"
    echo "TASK1_TOPOLOGY_AI_STARTED cpu=0 pid=$pid"
}

require_alive() {
    ai_pid="$(cat "$ai_pid_file")"
    communication_pid="$(cat "$communication_pid_file")"
    kill -0 "$ai_pid"
    kill -0 "$communication_pid"
    echo "TASK1_TOPOLOGY_WORKLOADS_ALIVE ai=true communication=true"
}

case "${1:-}" in
    p0)
        exec /usr/bin/task123-affinity 0 /bin/echo \
            'TASK1_TOPOLOGY_PROBE cpu=0 allowed=0'
        ;;
    p1)
        exec /usr/bin/task123-affinity 1 /bin/echo \
            'TASK1_TOPOLOGY_PROBE cpu=1 allowed=1'
        ;;
    probe)
        cpu="${2:-}"
        case "$cpu" in
            0|1) ;;
            *) echo "task123-topology: probe CPU must be 0 or 1" >&2; exit 2 ;;
        esac
        exec /usr/bin/task123-affinity "$cpu" /bin/echo \
            "TASK1_TOPOLOGY_PROBE cpu=$cpu allowed=$cpu"
        ;;
    c|start-communication)
        start_communication
        ;;
    wc|wait-communication)
        exec /usr/bin/task123-wait-log 120 \
            'TASK2_CONTROLLER_READY mode=task2' "$communication_log"
        ;;
    a|start-ai)
        start_ai
        ;;
    wa|wait-ai)
        /usr/bin/task123-wait-log 120 \
            'TASK3_INFER_STARTED model=yolo11n.ncnn' "$ai_log"
        grep -m1 'TASK3_MODEL_READY model=yolo11n.ncnn' "$ai_log"
        ;;
    l|workloads-alive)
        require_alive
        ;;
    f|affinity-evidence)
        grep -m1 'TASK1_TOPOLOGY_AFFINITY cpu=0 verified=true' "$ai_log"
        grep -m1 'TASK1_TOPOLOGY_AFFINITY cpu=1 verified=true' \
            "$communication_log"
        ;;
    i|inference-evidence)
        exec /usr/bin/task123-wait-log 240 \
            'TASK3_INFER model=yolo11n.ncnn' "$ai_log"
        ;;
    t|traffic-evidence)
        grep -m1 'STARRY_T2N1_CONTROL_SENT' "$communication_log"
        grep -m1 'STARRY_T2N1_ACK' "$communication_log"
        grep -E -m1 'STARRY_T2N1_STATUS_DELIVERED.*request=3([[:space:]]|$)' \
            "$communication_log"
        ;;
    *)
        echo "usage: task123-topology COMMAND" >&2
        exit 2
        ;;
esac
