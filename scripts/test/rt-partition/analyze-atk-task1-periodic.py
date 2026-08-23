#!/usr/bin/env python3
"""Extract Task 1 RT-Thread periodic samples and reproducible statistics."""

from __future__ import annotations

import argparse
import csv
import math
import re
import statistics
from pathlib import Path


ROW = re.compile(r"(\d+),(\d+),(\d+),(\d+),(\d+)")
INFER = re.compile(r"TASK3_INFER .*?infer_us=(\d+)")


def percentile_nearest_rank(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def parse(path: Path) -> tuple[list[tuple[int, int, int, int, int]], list[int]]:
    text = path.read_text(errors="replace")
    start = text.find("PERIODIC LATENCY START")
    end = text.find("PERIODIC LATENCY COMPLETE", start)
    if start < 0 or end < 0:
        raise ValueError(f"{path}: incomplete periodic latency block")
    rows = []
    for line in text[start:end].splitlines():
        match = ROW.fullmatch(line.strip())
        if match:
            rows.append(tuple(map(int, match.groups())))
    if not rows:
        raise ValueError(f"{path}: no periodic latency samples")
    if [row[0] for row in rows] != list(range(len(rows))):
        raise ValueError(f"{path}: non-contiguous periodic sample sequence")
    declared = re.search(r"PERIODIC LATENCY COMPLETE samples=(\d+)", text[end:])
    if declared is None or int(declared.group(1)) != len(rows):
        raise ValueError(f"{path}: declared sample count does not match CSV rows")
    return rows, [int(value) for value in INFER.findall(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for path in args.logs:
        rows, inference = parse(path)
        out = args.output_dir / f"{path.stem}-periodic.csv"
        with out.open("w", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("sequence", "timestamp_ns", "deadline_ns", "actual_ns", "jitter_ns"))
            writer.writerows(rows)
        values = [row[4] for row in rows]
        summaries.append(
            {
                "log": path.name,
                "samples": len(values),
                "min_ns": min(values),
                "mean_ns": round(statistics.mean(values)),
                "p50_ns": percentile_nearest_rank(values, 0.50),
                "p90_ns": percentile_nearest_rank(values, 0.90),
                "p95_ns": percentile_nearest_rank(values, 0.95),
                "p99_ns": percentile_nearest_rank(values, 0.99),
                "p99_9_ns": percentile_nearest_rank(values, 0.999),
                "max_ns": max(values),
                "over_1ms": sum(value > 1_000_000 for value in values),
                "over_10ms": sum(value > 10_000_000 for value in values),
                "inference_samples": len(inference),
                "inference_median_us": round(statistics.median(inference)) if inference else "",
            }
        )
    summary = args.output_dir / "periodic-summary.csv"
    with summary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
