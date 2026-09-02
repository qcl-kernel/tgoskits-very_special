#!/usr/bin/env python3
"""Verify QEMU evidence for the frozen Task 1 physical-board CPU roles."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

from verify_pcap import analyze
from verify_starry_task23 import (
    KIND_ACK,
    KIND_CONTROL,
    KIND_STATUS,
    STARRY_IP,
    ZEPHYR_IP,
    task2_frames,
)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SAMPLE_RE = re.compile(
    r"^(\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)\s*$", re.MULTILINE
)


def verify_static_contract(repo: Path) -> list[str]:
    test_dir = repo / "scripts/test/net-dual-guest"
    try:
        starry = load_toml(test_dir / "vm-aarch64-starry-task1-multivcpu.toml")
        zephyr = load_toml(test_dir / "vm-aarch64-zephyr-task1-multivcpu.toml")
        qemu = load_toml(
            test_dir / "qemu-aarch64-starry-zephyr-task1-multivcpu.toml"
        )
        starry_build = load_toml(
            repo
            / "apps/starry/starryos-task2/build-aarch64-unknown-none-softfloat.toml"
        )
        rr = load_toml(test_dir / "axvisor-qemu-starry-task1-rr.toml")
        fp_rr = load_toml(test_dir / "axvisor-qemu-starry-task1-fp-rr.toml")
    except (OSError, tomllib.TOMLDecodeError) as error:
        return [str(error)]

    failures: list[str] = []
    require_equal(failures, "StarryOS cpu_num", starry["base"].get("cpu_num"), 2)
    require_equal(
        failures,
        "StarryOS phys_cpu_ids",
        starry["base"].get("phys_cpu_ids"),
        [2, 1],
    )
    require_equal(
        failures,
        "StarryOS priority",
        starry["base"].get("host_sched_priority"),
        89,
    )
    require_equal(failures, "Zephyr cpu_num", zephyr["base"].get("cpu_num"), 1)
    require_equal(
        failures, "Zephyr phys_cpu_ids", zephyr["base"].get("phys_cpu_ids"), [1]
    )
    require_equal(
        failures, "Zephyr priority", zephyr["base"].get("host_sched_priority"), 90
    )
    require_equal(
        failures, "StarryOS build CPU capacity", starry_build.get("max_cpu_num"), 2
    )

    arguments = qemu.get("args", [])
    smp = option_value(arguments, "-smp") if isinstance(arguments, list) else None
    require_equal(failures, "QEMU pCPU count", smp, "3")

    rr_features = set(rr.pop("features", []))
    fp_rr_features = set(fp_rr.pop("features", []))
    if rr != fp_rr:
        failures.append("RR and FP-RR host configs differ outside features")
    if rr_features ^ fp_rr_features != {"rr-scheduler", "fp-rr-scheduler"}:
        failures.append("host feature difference is not exactly RR versus FP-RR")
    return failures


def verify_run(run_dir: Path, arm: str, sample_count: int) -> tuple[list[str], dict[str, int]]:
    log_path = run_dir / "run.log"
    try:
        log = ANSI_RE.sub("", log_path.read_text(errors="replace"))
    except OSError as error:
        return [str(error)], {}

    scheduler = "Round-robin" if arm == "rr" else "Fixed-priority round-robin"
    patterns = (
        ("scheduler marker", rf"use {scheduler} scheduler\."),
        ("StarryOS vCPU0", r"Spawning task for VM\[1\] VCpu\[0\]"),
        ("StarryOS vCPU1", r"Spawning task for VM\[1\] VCpu\[1\]"),
        ("Zephyr vCPU0", r"Spawning task for VM\[2\] VCpu\[0\]"),
        (
            "StarryOS vCPU0 priority/pCPU2",
            r'VM\[1\]-VCpu\[0\]"\) created priority=89 cpumask: \[2, \]',
        ),
        (
            "StarryOS vCPU1 priority/pCPU1",
            r'VM\[1\]-VCpu\[1\]"\) created priority=89 cpumask: \[1, \]',
        ),
        (
            "Zephyr vCPU0 priority/pCPU1",
            r'VM\[2\]-VCpu\[0\]"\) created priority=90 cpumask: \[1, \]',
        ),
        ("StarryOS vCPU0 execution", r"VM\[1\] VCpu\[0\] running on CPU2"),
        ("StarryOS vCPU1 execution", r"VM\[1\] VCpu\[1\] running on CPU1"),
        ("Zephyr vCPU0 execution", r"VM\[2\] VCpu\[0\] running on CPU1"),
        ("two online Guest CPUs", r"TASK1_TOPOLOGY_CPU_ONLINE count=2\b"),
        ("Guest CPU0 affinity probe", r"TASK1_TOPOLOGY_PROBE cpu=0 allowed=0\b"),
        ("Guest CPU1 affinity probe", r"TASK1_TOPOLOGY_PROBE cpu=1 allowed=1\b"),
        (
            "communication role on Guest CPU1",
            r"TASK1_TOPOLOGY_COMMUNICATION_STARTED cpu=1\b",
        ),
        ("AI role on Guest CPU0", r"TASK1_TOPOLOGY_AI_STARTED cpu=0\b"),
        ("communication endpoint ready", r"TASK2_CONTROLLER_READY mode=task2\b"),
        ("ncnn model ready", r"TASK3_MODEL_READY model=yolo11n\.ncnn runtime=ncnn"),
        ("ncnn inference", r"TASK3_INFER model=yolo11n\.ncnn[^\n]*request=1\b"),
        ("CONTROL transmitted", r"STARRY_T2N1_CONTROL_SENT\b"),
        ("CONTROL acknowledged", r"STARRY_T2N1_ACK\b"),
        ("STATUS delivered", r"STARRY_T2N1_STATUS_DELIVERED[^\n]*request=3\b"),
        (
            "live traffic throughout periodic sampling",
            rf"PERIODIC LATENCY SAMPLING COMPLETE samples={sample_count} "
            r"controls=[1-9]\d* statuses=[1-9]\d* heartbeats=[1-9]\d*",
        ),
        (
            "both workloads alive after sampling",
            r"TASK1_TOPOLOGY_WORKLOADS_ALIVE ai=true communication=true",
        ),
        ("AI workload affinity", r"TASK1_TOPOLOGY_AFFINITY cpu=0 verified=true"),
        (
            "communication workload affinity",
            r"TASK1_TOPOLOGY_AFFINITY cpu=1 verified=true",
        ),
        ("RT scheduler counters", r"RT vCPU wait counters:"),
        ("VM1 two-vCPU report", r"VCPUs:\s+2\b"),
        ("VM1 vCPU0 pCPU2 report", r"VCpu 0: Physical CPU mask 0x4, PCpu ID 2\b"),
        ("VM1 vCPU1 pCPU1 report", r"VCpu 1: Physical CPU mask 0x2, PCpu ID 1\b"),
        ("RT counter for vCPU0", r"\bvcpu=0\b"),
        ("RT counter for vCPU1", r"\bvcpu=1\b"),
    )
    failures = [
        f"{log_path}: missing {label}"
        for label, pattern in patterns
        if re.search(pattern, log) is None
    ]

    samples = [
        (int(sequence), int(jitter))
        for sequence, _, _, _, jitter in SAMPLE_RE.findall(log)
    ]
    if [sequence for sequence, _ in samples] != list(range(sample_count)):
        failures.append(
            f"{log_path}: expected one ordered {sample_count}-sample series, got {len(samples)} rows"
        )

    failures.extend(verify_pcaps(run_dir / "starry.pcap", run_dir / "zephyr.pcap"))
    metrics = {
        "samples": len(samples),
        "maximum_jitter_ns": max((jitter for _, jitter in samples), default=0),
        "deadline_misses_1ms": sum(jitter > 1_000_000 for _, jitter in samples),
    }
    return failures, metrics


def verify_pcaps(starry_path: Path, zephyr_path: Path) -> list[str]:
    try:
        starry = analyze(starry_path, None)
        zephyr = analyze(zephyr_path, None)
        frames = task2_frames(starry_path)
    except (OSError, ValueError) as error:
        return [str(error)]

    failures: list[str] = []
    if starry["task2_signature"] != zephyr["task2_signature"]:
        failures.append("StarryOS and Zephyr pcaps have different T2N1 ledgers")
    kinds = Counter(frame.kind for frame in frames)
    for kind, label in (
        (KIND_CONTROL, "CONTROL"),
        (KIND_STATUS, "STATUS"),
        (KIND_ACK, "ACK"),
    ):
        if kinds[kind] < 3:
            failures.append(f"pcap needs at least three {label} frames, got {kinds[kind]}")
    if not any(
        frame.src == STARRY_IP and frame.dst == ZEPHYR_IP and frame.kind == KIND_CONTROL
        for frame in frames
    ):
        failures.append("pcap has no StarryOS-to-Zephyr CONTROL")
    if not any(
        frame.src == ZEPHYR_IP and frame.dst == STARRY_IP and frame.kind == KIND_STATUS
        for frame in frames
    ):
        failures.append("pcap has no Zephyr-to-StarryOS STATUS")
    return failures


def verify_matrix(matrix_dir: Path) -> list[str]:
    run_dirs = sorted(
        path
        for path in matrix_dir.iterdir()
        if path.is_dir() and re.fullmatch(r"(?:rr|fp-rr)-\d{2}", path.name)
    )
    rr = [path for path in run_dirs if path.name.startswith("rr-")]
    fp_rr = [path for path in run_dirs if path.name.startswith("fp-rr-")]
    if not rr or len(rr) != len(fp_rr):
        return [f"matrix needs paired RR/FP-RR runs, got {len(rr)}/{len(fp_rr)}"]
    try:
        reference = read_hashes(run_dirs[0] / "equivalence-hashes.txt")
        return [
            f"{run_dir}: immutable artifact hashes differ between scheduler arms"
            for run_dir in run_dirs[1:]
            if read_hashes(run_dir / "equivalence-hashes.txt") != reference
        ]
    except (OSError, ValueError) as error:
        return [str(error)]


def load_toml(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def option_value(arguments: list, option: str) -> object | None:
    positions = [index for index, value in enumerate(arguments) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        return None
    return arguments[positions[0] + 1]


def require_equal(failures: list[str], label: str, actual: object, expected: object) -> None:
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def read_hashes(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, separator, digest = line.partition("=")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{path}: invalid hash record {line!r}")
        hashes[name] = digest
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--arm", choices=("rr", "fp-rr"))
    parser.add_argument("--sample-count", type=int)
    parser.add_argument("--matrix", type=Path)
    args = parser.parse_args()

    failures = verify_static_contract(args.repo)
    metrics: dict[str, int] = {}
    if args.run_dir is not None or args.arm is not None or args.sample_count is not None:
        if args.run_dir is None or args.arm is None or args.sample_count is None:
            parser.error("--run-dir, --arm, and --sample-count must be provided together")
        run_failures, metrics = verify_run(args.run_dir, args.arm, args.sample_count)
        failures.extend(run_failures)
    if args.matrix is not None:
        failures.extend(verify_matrix(args.matrix))

    if failures:
        print("FAIL: Task 1 multi-vCPU evidence")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    if args.run_dir is not None:
        (args.run_dir / "topology-verification.json").write_text(
            json.dumps({"status": "pass", "arm": args.arm, **metrics}, indent=2) + "\n"
        )
    print("PASS: Task 1 QEMU topology matches the frozen board CPU-role contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
