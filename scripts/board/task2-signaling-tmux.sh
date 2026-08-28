#!/usr/bin/env bash
# Two-pane live replay of Task-2 T2N1 sender/receiver signaling in tmux.
#
# The physical board multiplexes AxVisor + both guests onto one UART, so the
# two endpoints are split on the host: task2-signaling-replay.py classifies the
# captured console stream and writes sender/receiver/status feeds, and this
# wrapper lays them out in tmux:
#
#   ┌──────────────────────┬─────────────────────┐
#   │ [发送方 controller    │ [接收方 managed      │
#   │  StarryOS 10.0.42.15] │  Zephyr 10.0.42.2]  │
#   │  REQ→ 发出 CONTROL    │  REQ← 收到 CONTROL  │
#   │  ANS← 收到 ACK/STATUS │  ANS→ 回 ACK/STATUS │
#   ├──────────────────────┴─────────────────────┤
#   │ [LINK STATUS]  heartbeat / SAFE / recovery │
#   └────────────────────────────────────────────┘
#
# Usage: task2-signaling-tmux.sh <console.log> [--speed N] [--heartbeats=…]
#                                  [--context=…]
#   pass --speed 0 for an instant dump of the whole log.
set -euo pipefail

log="${1:?usage: task2-signaling-tmux.sh <console.log> [--speed N] ...}"
shift
session="t2n1-demo"
out="/tmp/t2n1-demo/feeds"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
replay="$script_dir/task2-signaling-replay.py"
viewer="$script_dir/task2-signaling-view.sh"

rm -rf "$out"
mkdir -p "$out"

tmux kill-session -t "$session" 2>/dev/null || true
tmux new-session -d -s "$session" -x 220 -y 60
tmux set-option -t "$session" status off

nohup python3 "$replay" "$log" --out-dir "$out" "$@" \
    >"$out/producer.log" 2>&1 &
producer_pid=$!

# I_I layout: split the window into a full-width bottom strip first, then
# split the remaining top area into the two main columns.  A single tmux
# window cannot give a full-width bottom pane any other way.
tmux split-window -v -l 12 -t "$session"
tmux select-pane -t "$session" -U
tmux split-window -h -t "$session"

# Identify the three panes by geometry so the wrapper is index-independent:
# the bottom strip is the pane with the largest top offset, and the two top
# panes split left/right.
mapfile -t geometry < <(tmux list-panes -t "$session" -F '#{pane_id} #{pane_left} #{pane_top}')
sender_pane=""
receiver_pane=""
status_pane=""
for entry in "${geometry[@]}"; do
    read -r id left top <<<"$entry"
    if [[ -z "$status_pane" ]] || ((top > status_top)); then
        status_pane="$id"
        status_top="$top"
    fi
    if [[ -z "$sender_pane" ]] || ((left < sender_left)); then
        sender_pane="$id"
        sender_left="$left"
    fi
done
for entry in "${geometry[@]}"; do
    read -r id left top <<<"$entry"
    if [[ "$id" != "$status_pane" && "$id" != "$sender_pane" ]]; then
        receiver_pane="$id"
    fi
done

tmux send-keys -t "$sender_pane" \
    "$viewer '$out/sender.feed' '[发送方 controller · StarryOS 10.0.42.15:4242]' 40 '48;5;23' 44" Enter
tmux send-keys -t "$receiver_pane" \
    "$viewer '$out/receiver.feed' '[接收方 managed · Zephyr 10.0.42.2:4242]' 40 '48;5;22' 42" Enter
tmux send-keys -t "$status_pane" \
    "$viewer '$out/status.feed' '[LINK STATUS]' 12" Enter

tmux select-pane -t "$sender_pane"
tmux attach -t "$session" 2>/dev/null || true

wait "$producer_pid" 2>/dev/null || true
echo "replay finished; feeds in $out"