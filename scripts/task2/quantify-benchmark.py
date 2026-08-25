#!/usr/bin/env python3
"""Strictly quantify one bounded T2N1 physical-board benchmark window."""

import argparse
import json
import math
import re
import statistics
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VM1 = re.compile(r"^\[VM 1\] ?", re.MULTILINE)
CONTROL = re.compile(
    r"TASK2_BENCHMARK_CONTROL_SENT elapsed_ms=(\d+) request=(\d+) seq=(\d+) value=(-?\d+)"
)
STATUS = re.compile(
    r"TASK2_BENCHMARK_STATUS_RECEIVED elapsed_ms=(\d+) request=(\d+) sample=(\d+) "
    r"rtt_ms=(\d+) state=(\w+) value=(-?\d+)"
)
COMPLETE = re.compile(
    r"TASK2_BENCHMARK_COMPLETE transactions=(\d+) statuses=(\d+) acks=(\d+) "
    r"retransmissions=(\d+) elapsed_ms=(\d+) throughput_milli_tps=(\d+)"
)
FAILURES = (
    "TASK2_ERROR=",
    "TASK2_PROTOCOL_ERROR",
    "TASK2_SAFE",
    "ESR_EL2",
    "panic",
    "segmentation fault",
)


def percentile(values: list[int], probability: float) -> int:
    index = max(0, math.ceil(len(values) * probability) - 1)
    return sorted(values)[index]


def parse(path: Path, transactions: int) -> dict[str, int | float]:
    text = VM1.sub("", ANSI.sub("", path.read_text(errors="replace")))
    begin_marker = (
        f"TASK2_BENCHMARK_BEGIN transactions={transactions} "
        "protocol=T2N1 mode=stop-and-wait"
    )
    end_marker = f"TASK2_BENCHMARK_END transactions={transactions} pending=0 errors=0"
    begin = text.rfind(begin_marker)
    end = text.find(end_marker, begin)
    if begin < 0 or end < begin:
        raise ValueError(f"{path}: latest bounded BEGIN/END window is incomplete")
    window = text[begin : end + len(end_marker)]
    for marker in FAILURES:
        if marker in window:
            raise ValueError(f"{path}: failure marker in benchmark window: {marker}")

    controls = [tuple(map(int, match)) for match in CONTROL.findall(window)]
    statuses = [tuple(map(int, match[:4])) for match in STATUS.findall(window)]
    if len(controls) != transactions or len(statuses) != transactions:
        raise ValueError(
            f"{path}: expected {transactions} CONTROL/STATUS rows, "
            f"found {len(controls)}/{len(statuses)}"
        )
    expected = list(range(1, transactions + 1))
    if [row[1] for row in controls] != expected:
        raise ValueError(f"{path}: CONTROL request sequence is incomplete or out of order")
    if [row[1] for row in statuses] != expected or [row[2] for row in statuses] != expected:
        raise ValueError(f"{path}: STATUS request/sample sequence is incomplete or out of order")

    completions = COMPLETE.findall(window)
    if len(completions) != 1:
        raise ValueError(f"{path}: expected exactly one benchmark completion")
    count, status_count, ack_count, retransmissions, elapsed_ms, throughput = map(
        int, completions[0]
    )
    if (count, status_count, ack_count) != (transactions, transactions, transactions):
        raise ValueError(f"{path}: benchmark completion counters do not close")
    rtts = [row[3] for row in statuses]
    return {
        "transactions": transactions,
        "statuses": status_count,
        "acks": ack_count,
        "retransmissions": retransmissions,
        "elapsed_ms": elapsed_ms,
        "throughput_tps": throughput / 1000,
        "mean_rtt_ms": statistics.mean(rtts),
        "p50_rtt_ms": statistics.median(rtts),
        "p95_rtt_ms": percentile(rtts, 0.95),
        "p99_rtt_ms": percentile(rtts, 0.99),
        "max_rtt_ms": max(rtts),
        "error_rate": 0.0,
        "retransmission_rate": retransmissions / transactions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--transactions", type=int, default=200)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = parse(args.log, args.transactions)
    output = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(output)
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
