#!/usr/bin/env bash
# Colorized tail viewer for one T2N1 signaling feed.
#
# Usage: task2-signaling-view.sh <feed> <title> [lines] [bg] [headerbg]
#   feed       absolute path of a *-feed produced by task2-signaling-replay.py
#   title      header shown once at the top of the pane
#   lines      initial history lines (default 40)
#   bg         ANSI background (e.g. 48;5;23) applied to every line, so the two
#              endpoint panes read as categorically different colors
#   headerbg   ANSI background for the header (default 44 = blue)
set -u

feed="$1"
title="$2"
lines="${3:-40}"
bg="${4:-}"
headerbg="${5:-44}"

printf '\033[1;37;%sm %s \033[0m\n' "$headerbg" "$title"
tail -n "$lines" -F -- "$feed" 2>/dev/null |
while IFS= read -r line; do
    tag="${line%% *}"
    case "$tag" in
        *→) c=32 ;;   # outbound (REQ→ / ANS→)
        *←) c=36 ;;   # inbound  (ANS← / REQ← / ACK←)
        EV) c=33 ;;
        HB) c=90 ;;
        ST) c="1;97" ;;
        *)  c=0 ;;
    esac
    if [[ -n "$bg" ]]; then
        printf '\033[%s;%sm%s\033[0m\n' "$c" "$bg" "$line"
    else
        printf '\033[%sm%s\033[0m\n' "$c" "$line"
    fi
done