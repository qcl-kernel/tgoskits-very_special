#!/usr/bin/env python3
"""Render the T2N1 signaling exchange as a timeline chart.

X-axis is protocol time (seconds since task2-net start).  Two lanes split the
y-axis — 发送方 (StarryOS controller) on top, 接收方 (Zephyr) on the bottom —
and every CONTROL / STATUS / ACK message travels as a colored vertical arrow
between the lanes, so the request direction, the response, the ACK and the RTT
are all visible at a glance.

This is the same role-relative view as the tmux demo, as a static figure.

Usage:
    task2-signaling-chart.py <log>... [--out chart.png] [--zoom START END]
                                   [--dpi 150] [--height 7]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as font_manager
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, Rectangle

_SELF = Path(__file__).resolve()
_REPLAY = _SELF.parent / "task2-signaling-replay.py"

_spec = importlib.util.spec_from_file_location("t2n1_replay", _REPLAY)
R = importlib.util.module_from_spec(_spec)
sys.modules["t2n1_replay"] = R
_spec.loader.exec_module(R)

for _f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
           "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"):
    if Path(_f).exists():
        try:
            font_manager.fontManager.addfont(_f)
        except Exception:
            pass
matplotlib.rcParams["font.family"] = "Noto Sans CJK SC"

KIND_COLOR = {
    "CONTROL": "#2ecc71",
    "STATUS": "#e67e22",
    "ACK": "#00bcd4",
    "重发": "#e74c3c",
    "心跳": "#bdbdbd",
}


def kind_of(message: str) -> str | None:
    if "RETRANSMIT" in message:
        return "重发"
    if "HEARTBEAT" in message:
        return "心跳"
    if "ACK" in message:
        return "ACK"
    if "STATUS" in message:
        return "STATUS"
    if "CONTROL" in message:
        return "CONTROL"
    return None


def load_events(logs: list[Path], context: str):
    wanted = set(R.SENDER_MARKERS) | set(R.RECEIVER_MARKERS) | set(R.CONTROLLER_OLD_MARKERS) | {
        "TASK2_READY", "TASK2_ACK", "TASK2_RETRANSMIT", "TASK2_HEARTBEAT_RECEIVED",
        "TASK2_SAFE", "TASK2_RECOVERED", "TASK2_DUPLICATE", "TASK2_PROTOCOL_ERROR",
        "TASK2_REMOTE_ERROR", "TASK2_REJECTED", "TASK2_SESSION_MISMATCH",
    }
    lines = list(R.iter_signaling_lines(logs, wanted))
    state = R.ClassifyState()
    events: list = []
    for line in lines:
        result = R.classify(line, state)
        if result is None:
            continue
        side, direction, message = result
        if direction == "EV":
            message = R.emit_message(message, context)
            if message is None:
                continue
        events.append((side, direction, message, R.proto_time(message)))
    return R.interleave_events(events)


def lane_y(side: str) -> float:
    return 1.0 if side == "sender" else 0.0


def draw_axes(ax, xmin: float | None, xmax: float | None) -> None:
    ax.add_patch(Rectangle((-1e9, 0.48), 2e9, 0.54, color="#dbe9f7", zorder=0))
    ax.add_patch(Rectangle((-1e9, -0.02), 2e9, 0.50, color="#d9f2d9", zorder=0))
    ax.axhline(1.0, color="#4a7db4", lw=1.2, zorder=1)
    ax.axhline(0.0, color="#3f8f3f", lw=1.2, zorder=1)
    ax.set_yticks([1.0, 0.0])
    ax.set_yticklabels(["发送方 (StarryOS 10.0.42.15)", "接收方 (Zephyr 10.0.42.2)"])
    ax.set_xlabel("协议时间 (s)")
    ax.set_ylim(-0.5, 1.5)
    if xmin is not None and xmax is not None:
        ax.set_xlim(xmin, xmax)
    ax.grid(axis="x", ls=":", color="#cccccc", zorder=0)


def plot_chart(events, ax, xmin=None, xmax=None) -> None:
    draw_axes(ax, xmin, xmax)
    plotted: set[str] = set()
    for side, direction, message, t_ms in events:
        if t_ms is None:
            continue
        kind = kind_of(message)
        if kind is None:
            if "SAFE" in message:
                ax.axvline(t_ms / 1000, color="#c0392b", ls="--", lw=1.4, zorder=3)
                ax.text(t_ms / 1000, 1.22, "Safe", color="#c0392b", ha="center",
                        fontsize=7, zorder=4)
            elif "RECOVER" in message:
                ax.axvline(t_ms / 1000, color="#8e44ad", ls="--", lw=1.4, zorder=3)
                ax.text(t_ms / 1000, 1.22, "恢复", color="#8e44ad", ha="center",
                        fontsize=7, zorder=4)
            continue
        if kind == "心跳":
            if direction == "RX":
                ax.scatter([t_ms / 1000], [lane_y(side)], s=6, color="#bdbdbd",
                           marker="x", zorder=2)
            continue
        color = KIND_COLOR[kind]
        if direction == "TX":
            y0, y1 = lane_y(side), 1.0 - lane_y(side)
            ax.add_patch(FancyArrowPatch(
                (t_ms / 1000, y0), (t_ms / 1000, y1),
                arrowstyle="-|>", mutation_scale=9, lw=0.9,
                color=color, zorder=3))
        else:
            ax.scatter([t_ms / 1000], [lane_y(side)], s=16, color=color, zorder=3)
        plotted.add(kind)
    handles = [plt.Line2D([], [], color=KIND_COLOR[k], lw=2, label=k)
               for k in ("CONTROL", "STATUS", "ACK", "重发")]
    handles += [plt.Line2D([], [], color="#bdbdbd", marker="x", ls="", label="心跳")]
    handles += [plt.Line2D([], [], color="#c0392b", ls="--", label="Safe/异常"),
                plt.Line2D([], [], color="#8e44ad", ls="--", label="恢复")]
    ax.legend(handles=handles, loc="upper right", fontsize=8, ncol=4,
              framealpha=0.95)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--out", default="t2n1-chart.png", type=Path)
    parser.add_argument("--zoom", nargs=2, type=float, metavar=("START", "END"))
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--context", choices=("on", "brief", "off"), default="brief")
    args = parser.parse_args()
    for path in args.logs:
        if not path.is_file():
            parser.error(f"log not found: {path}")

    events = load_events(args.logs, args.context)
    if not events:
        print("no signaling events", file=sys.stderr)
        return 1
    tmax = max(e[3] for e in events if e[3] is not None) / 1000

    fig, ax = plt.subplots(figsize=(13, 5.2))
    plot_chart(events, ax,
               xmin=args.zoom[0] if args.zoom else 0,
               xmax=args.zoom[1] if args.zoom else tmax)
    n_tx = sum(1 for e in events if e[1] in ("TX", "RX"))
    title = "T2N1 双端信令时间线"
    if args.zoom:
        title += f" · 窗口 {args.zoom[0]:.2f}–{args.zoom[1]:.2f}s"
    else:
        title += f" · 全时段 {tmax:.2f}s · {n_tx} 条消息"
    ax.set_title(title, fontsize=12, pad=10)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=args.dpi, bbox_inches="tight")
    print(f"chart -> {args.out}  ({n_tx} messages, span {tmax:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())