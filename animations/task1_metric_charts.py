"""Task 1 per-metric static charts.

Latency metrics (mean/p50/p95/p99/p99_9/max): time-axis plot with the two
latency curves rolling and the two dashed reference lines for that metric.
Count metrics (>1ms/>10ms/deadline): RR vs FP-RR comparison bars.

Data: matrix-v8-only-3x3 official medians + latency series.
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "task1.json")
OUT = os.path.join(HERE, "output", "metrics")
os.makedirs(OUT, exist_ok=True)

plt.rcParams["font.family"] = ["Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ORANGE = "#e8590c"
GREEN = "#2b8a3e"
RED = "#e03131"
MUTED = "#495057"
YELLOW = "#f08c00"
BG = "white"
GRID = "#dee2e6"
WHITE = "#212529"
PANEL = "#f1f3f5"

YMAX = 10.0


def load():
    with open(DATA) as f:
        d = json.load(f)
    rr = np.array([r["jitter_ns"] / 1e6 for r in d["rr"]["series"]])
    fp = np.array([r["jitter_ns"] / 1e6 for r in d["fp_rr"]["series"]])
    med = d["medians"]
    return rr, fp, med


def latency_chart(rr, fp, med, metric, title_zh, filename, ymax=YMAX):
    fig, ax = plt.subplots(figsize=(13.5, 6.4), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.6)
    ax.set_ylim(0, ymax)
    ax.set_xlim(0, 60)
    ax.set_xlabel("时间 (s)", color=MUTED, fontsize=11)
    ax.set_ylabel("周期任务延迟 jitter (ms)", color=MUTED, fontsize=11)

    t = np.arange(len(rr)) * 0.01
    rr_v = med["rr"][metric]
    fp_v = med["fp_rr"][metric]
    ax.plot(t, rr, color=ORANGE, linewidth=1.0, alpha=0.9,
            label="RR（%s = %.3f ms）" % (title_zh, rr_v), zorder=4)
    ax.plot(t, fp, color=GREEN, linewidth=1.4, alpha=0.95,
            label="bounded FP-RR（%s = %.3f ms）" % (title_zh, fp_v), zorder=5)

    ax.axhline(rr_v, color=ORANGE, linestyle="--", linewidth=1.6, alpha=0.9)
    ax.axhline(fp_v, color=GREEN, linestyle="--", linewidth=1.6, alpha=0.9)
    ax.set_title("Task 1 · %s 指标：RR vs FP-RR（实测数据重放 · 6000×10 ms）"
                 % title_zh, color=WHITE, fontsize=15, pad=10)
    ax.legend(loc="upper right", facecolor=PANEL, edgecolor=GRID, fontsize=11, framealpha=0.95)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, filename), facecolor=BG, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    print("OK", filename)


def count_chart(med, metric, title_zh, filename):
    rr_v = int(med["rr"].get(metric, 0))
    fp_v = int(med["fp_rr"].get(metric, 0))
    fig, ax = plt.subplots(figsize=(13.5, 6.4), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(color=GRID, linewidth=0.6, alpha=0.6, axis="y")
    bars = ax.bar(["RR", "bounded FP-RR"], [rr_v, fp_v],
                  color=[ORANGE, GREEN], width=0.5, zorder=3)
    ax.set_ylabel("次数", color=MUTED, fontsize=11)
    for b, v in zip(bars, [rr_v, fp_v]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.1, "%d" % v,
                color=WHITE, fontsize=13, ha="center")
    ax.set_title("Task 1 · %s：RR vs FP-RR（6000 样本中）" % title_zh,
                 color=WHITE, fontsize=15, pad=10)
    ax.tick_params(axis="x", colors=WHITE, labelsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, filename), facecolor=BG, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
    print("OK", filename)


def main():
    rr, fp, med = load()
    latency_chart(rr, fp, med, "mean_ms", "mean", "Task1-mean.png")
    latency_chart(rr, fp, med, "p50_ms", "P50", "Task1-p50.png")
    latency_chart(rr, fp, med, "p95_ms", "P95", "Task1-p95.png")
    latency_chart(rr, fp, med, "p99_ms", "P99", "Task1-p99.png")
    latency_chart(rr, fp, med, "p99_9_ms", "P99.9", "Task1-p999.png")
    latency_chart(rr, fp, med, "max_ms", "max", "Task1-max.png")
    count_chart(med, "over_1ms", "超过 1 ms 次数", "Task1-over-1ms.png")
    count_chart(med, "over_10ms", "超过 10 ms 次数", "Task1-over-10ms.png")
    count_chart(med, "deadline_misses", "deadline miss 次数", "Task1-deadline-misses.png")
    print("ALL DONE ->", OUT)


if __name__ == "__main__":
    main()