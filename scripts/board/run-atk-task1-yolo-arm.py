#!/usr/bin/env python3
"""Drive one physical-board Task 1 YOLO/periodic arm over the sole UART."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import serial


GUEST_PROMPT = rb"root@starry:[^\r\n]*#"
HOST_PROMPT = rb"axvisor:/\$"
INFERENCE_COMPLETE = rb"TASK3_INFER [^\r\n]*model=yolo11n\.ncnn"
PAYLOAD_PRESSURE_PROGRESS = rb"TASK1_PRESSURE_PROGRESS [^\r\n]*alive=1\b"
COMMUNICATION_STATUS_RECEIVED = rb"TASK3_STATUS_RECEIVED [^\r\n]*\brequest=[0-9]+\b"
PERIODIC_DUMP_COMMAND = b"d"
POST_SAMPLING_INFERENCE_TIMEOUT_SECONDS = 30
POST_SAMPLING_COMMUNICATION_TIMEOUT_SECONDS = 30
# RT-Thread formats and writes every retained row after sampling. Budget for a
# deliberately conservative rate below the ~118 rows/s observed on RK3588 so
# serial export cannot consume the sampling timeout.
MIN_SERIAL_EXPORT_ROWS_PER_SECOND = 50


@dataclass(frozen=True)
class RunConfig:
    log_path: Path
    metadata_path: Path
    port: str
    baud: int
    scheduler: str
    runtime_seconds: int
    expected_inferences: int
    expected_samples: int
    period_ms: int
    completion_grace_seconds: int
    artifacts: tuple[Path, ...]
    load_mode: str = "yolo"
    workload_start: str = "guest-shell"
    periodic_guest: str = "rtthread"
    dump_chunk_rows: int = 256

    @property
    def model_mode(self) -> str:
        if self.load_mode == "idle":
            return "idle"
        return "model-loop" if self.runtime_seconds > 0 else "model-only"

    @property
    def periodic_timeout_seconds(self) -> int:
        nominal_seconds = self.expected_samples * self.period_ms / 1000
        minimum_seconds = max(nominal_seconds, self.runtime_seconds)
        sampling_budget = math.ceil(minimum_seconds * 3)
        export_budget = math.ceil(
            self.expected_samples / MIN_SERIAL_EXPORT_ROWS_PER_SECOND
        )
        return max(
            90,
            sampling_budget + export_budget + self.completion_grace_seconds,
        )


class Console:
    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.serial = serial.Serial(port, baudrate=baud, timeout=0.1)
        self.log = log_path.open("wb")
        self.buffer = bytearray()

    def close(self) -> None:
        self.serial.close()
        self.log.close()

    def note(self, message: str) -> None:
        line = f"\nTASK1_RUNNER {message}\n".encode()
        self.log.write(line)
        self.log.flush()
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()

    def drain(self, seconds: float = 0.2) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.serial.read(65536)
            if not chunk:
                continue
            self.log.write(chunk)
            self.log.flush()
            # Keep bulk CSV off stdout. A long unattended PTY can stop accepting
            # output, which would block this sole UART reader and invalidate the
            # physical-board run. Stage updates still reach stdout via note().
            self.buffer.extend(chunk)
            if len(self.buffer) > 4_000_000:
                del self.buffer[:-2_000_000]

    def expect(self, expression: bytes, timeout: float) -> None:
        pattern = re.compile(expression, re.DOTALL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain(0.2)
            if pattern.search(self.buffer):
                return
        raise TimeoutError(f"timeout waiting for {expression!r}")

    def expect_count(self, expression: bytes, count: int, timeout: float) -> None:
        pattern = re.compile(expression)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain(0.2)
            if len(pattern.findall(self.buffer)) >= count:
                return
        observed = len(pattern.findall(self.buffer))
        raise TimeoutError(
            f"timeout waiting for {count} matches of {expression!r}; observed {observed}"
        )

    def clear_match_window(self) -> None:
        self.buffer.clear()

    def raw(self, data: bytes) -> None:
        self.serial.write(data)
        self.serial.flush()

    def line(self, command: str) -> None:
        for byte in command.encode() + b"\r":
            self.raw(bytes((byte,)))
            time.sleep(0.002)

    def command(self, command: str, prompt: bytes, timeout: float = 30) -> None:
        self.clear_match_window()
        self.line(command)
        self.expect(prompt, timeout)

    def detach(self) -> None:
        self.clear_match_window()
        self.raw(b"\x18h")
        self.expect(HOST_PROMPT, 10)


def main() -> int:
    config = parse_arguments()
    write_metadata(config, "started")
    console = Console(config.port, config.baud, config.log_path)
    started = time.monotonic()
    try:
        console.note(
            f"scheduler={config.scheduler} model_mode={config.model_mode} "
            f"runtime_seconds={config.runtime_seconds} "
            f"expected_inferences={config.expected_inferences} "
            f"expected_samples={config.expected_samples} period_ms={config.period_ms}"
        )
        if config.workload_start == "payload-init":
            prepare_payload_started_workload(console, config.load_mode)
            start_periodic_probe(console, host_ready=True)
        else:
            prepare_starry_guest(console)
            if config.load_mode == "yolo":
                start_model_workload(console, config.model_mode)
            start_periodic_probe(console)

        measurement_started = time.monotonic()
        wait_for_sampling_complete(console, config)
        measurement_seconds = time.monotonic() - measurement_started
        console.note(f"periodic_elapsed_seconds={measurement_seconds:.3f}")

        collect_host_diagnostics(console)
        if config.load_mode == "yolo":
            if config.workload_start == "payload-init":
                collect_payload_pressure_progress(console)
            else:
                collect_model_results(console, config)
        collect_periodic_results(console, config)
        console.drain(1)
    except Exception as error:
        console.note(f"status=failed error={error!r}")
        console.drain(2)
        write_metadata(config, "failed", error=str(error))
        print(f"\nTASK1_ARM_ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        console.close()

    elapsed_seconds = time.monotonic() - started
    write_metadata(config, "complete", elapsed_seconds=elapsed_seconds)
    print("\nTASK1_ARM_COMPLETE")
    return 0


def prepare_starry_guest(console: Console) -> None:
    console.raw(b"\r")
    console.expect(GUEST_PROMPT, 60)
    console.command(
        "wc -c /proc/initrd; sha256sum /proc/initrd; "
        "mkdir -p /tmp/t1; cd /tmp/t1; "
        "gzip -dc /proc/initrd | cpio -id; "
        "mount --bind /tmp/t1/usr/share /usr/share; "
        "ip addr add 10.0.42.15/24 dev eth0 2>/dev/null || true; "
        "echo TASK1_SETUP_DONE",
        GUEST_PROMPT,
        90,
    )


def prepare_payload_started_workload(console: Console, load_mode: str) -> None:
    """Leave an init-driven Starry workload and return to the AxVisor shell."""
    console.raw(b"\r")
    if load_mode == "yolo":
        console.clear_match_window()
        console.expect(
            PAYLOAD_PRESSURE_PROGRESS,
            POST_SAMPLING_INFERENCE_TIMEOUT_SECONDS,
        )
        console.expect(
            COMMUNICATION_STATUS_RECEIVED,
            POST_SAMPLING_COMMUNICATION_TIMEOUT_SECONDS,
        )
    console.detach()


def wait_for_sampling_complete(console: Console, config: RunConfig) -> None:
    """Require live communication during a full hybrid pressure sample."""
    expression = (
        rb"PERIODIC LATENCY SAMPLING COMPLETE samples="
        + str(config.expected_samples).encode()
        + rb"\b"
    )
    if (
        config.load_mode == "yolo"
        and config.workload_start == "payload-init"
        and config.periodic_guest == "zephyr"
    ):
        expression = (
            rb"PERIODIC LATENCY SAMPLING COMPLETE samples="
            + str(config.expected_samples).encode()
            + rb" controls=[1-9][0-9]* statuses=[1-9][0-9]* heartbeats=[0-9]+\b"
        )
    console.expect(expression, config.periodic_timeout_seconds)


def start_model_workload(console: Console, model_mode: str) -> None:
    console.clear_match_window()
    console.line(f"/tmp/t1/bin/task2-net {model_mode}")
    console.expect(rb"TASK3_MODEL_READY model=yolo11n\.ncnn runtime=ncnn", 30)
    console.expect(rb"TASK3_INFER_STARTED", 30)


def start_periodic_probe(console: Console, *, host_ready: bool = False) -> None:
    if not host_ready:
        console.detach()
    console.command("vm list", HOST_PROMPT)
    console.command("vm console 2", rb"Attached VM\[2\] console", 30)
    console.clear_match_window()
    console.raw(b"g")
    console.expect(rb"PERIODIC LATENCY START", 10)


def collect_host_diagnostics(console: Console) -> None:
    console.detach()
    console.command("rt stat", HOST_PROMPT, 30)
    console.command("vmexit stat", HOST_PROMPT, 30)
    console.command("vm list", HOST_PROMPT, 30)


def collect_model_results(console: Console, config: RunConfig) -> None:
    console.command("vm console 1", rb"Attached VM\[1\] console", 30)
    console.expect_count(INFERENCE_COMPLETE, config.expected_inferences, 180)
    if config.model_mode == "model-loop":
        # The RT guest is blocked at the sampling/export barrier. Require one
        # fresh completion now so the board run proves AI progress without the
        # high-priority CSV exporter competing for the shared pCPU.
        console.clear_match_window()
        console.expect(
            INFERENCE_COMPLETE, POST_SAMPLING_INFERENCE_TIMEOUT_SECONDS
        )
        console.clear_match_window()
        console.raw(b"\x03")
        console.expect(GUEST_PROMPT, 30)
    else:
        console.expect(GUEST_PROMPT, 30)
    console.detach()


def collect_payload_pressure_progress(console: Console) -> None:
    """Prove that the init-driven NPU loop is still progressing after sampling."""
    console.command("vm console 1", rb"Attached VM\[1\] console", 30)
    console.clear_match_window()
    console.expect(
        PAYLOAD_PRESSURE_PROGRESS,
        POST_SAMPLING_INFERENCE_TIMEOUT_SECONDS,
    )
    console.expect(
        COMMUNICATION_STATUS_RECEIVED,
        POST_SAMPLING_COMMUNICATION_TIMEOUT_SECONDS,
    )
    console.detach()


def collect_periodic_results(console: Console, config: RunConfig) -> None:
    console.command("vm console 2", rb"Attached VM\[2\] console", 30)
    console.clear_match_window()
    if config.periodic_guest == "zephyr":
        dump_end = 0
        while dump_end < config.expected_samples:
            dump_end = min(
                dump_end + config.dump_chunk_rows,
                config.expected_samples,
            )
            console.raw(PERIODIC_DUMP_COMMAND)
            console.expect(
                rb"PERIODIC LATENCY CHUNK end=" + str(dump_end).encode() + rb"\b",
                config.periodic_timeout_seconds,
            )
    else:
        console.raw(PERIODIC_DUMP_COMMAND)
    console.expect(
        rb"PERIODIC LATENCY COMPLETE samples="
        + str(config.expected_samples).encode()
        + rb"\b",
        config.periodic_timeout_seconds,
    )


def parse_arguments() -> RunConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=1_500_000)
    parser.add_argument("--scheduler", choices=("rr", "fp-rr"), default="rr")
    parser.add_argument("--runtime-seconds", type=int, default=0)
    parser.add_argument("--load-mode", choices=("idle", "yolo"), default="yolo")
    parser.add_argument(
        "--workload-start",
        choices=("guest-shell", "payload-init"),
        default="guest-shell",
    )
    parser.add_argument("--expected-inferences", type=int)
    parser.add_argument("--expected-samples", type=int, default=300)
    parser.add_argument("--period-ms", type=int, default=10)
    parser.add_argument("--dump-chunk-rows", type=int, default=256)
    parser.add_argument(
        "--periodic-guest",
        choices=("rtthread", "zephyr"),
        default="rtthread",
    )
    parser.add_argument("--completion-grace-seconds", type=int, default=180)
    parser.add_argument("--artifact", action="append", default=[], type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()

    expected_inferences = args.expected_inferences
    if expected_inferences is None:
        if args.load_mode == "idle":
            expected_inferences = 0
        else:
            expected_inferences = 50 if args.runtime_seconds > 0 else 1
    positive_values = {
        "baud": args.baud,
        "expected_samples": args.expected_samples,
        "period_ms": args.period_ms,
        "dump_chunk_rows": args.dump_chunk_rows,
        "completion_grace_seconds": args.completion_grace_seconds,
    }
    invalid = [name for name, value in positive_values.items() if value <= 0]
    if args.runtime_seconds < 0:
        invalid.append("runtime_seconds")
    if expected_inferences < 0 or (args.load_mode == "yolo" and expected_inferences == 0):
        invalid.append("expected_inferences")
    if invalid:
        parser.error(f"values must be positive (runtime may be zero): {', '.join(invalid)}")
    nominal_runtime_ms = args.expected_samples * args.period_ms
    requested_runtime_ms = args.runtime_seconds * 1000
    if nominal_runtime_ms < requested_runtime_ms:
        parser.error(
            "expected_samples * period_ms must cover runtime_seconds: "
            f"{nominal_runtime_ms}ms < {requested_runtime_ms}ms"
        )

    missing_artifacts = [path for path in args.artifact if not path.is_file()]
    if missing_artifacts:
        parser.error(f"artifact does not exist: {missing_artifacts[0]}")
    metadata_path = args.metadata or args.log.with_name(f"{args.log.name}.metadata.txt")
    return RunConfig(
        log_path=args.log,
        metadata_path=metadata_path,
        port=args.port,
        baud=args.baud,
        scheduler=args.scheduler,
        runtime_seconds=args.runtime_seconds,
        expected_inferences=expected_inferences,
        expected_samples=args.expected_samples,
        period_ms=args.period_ms,
        completion_grace_seconds=args.completion_grace_seconds,
        artifacts=tuple(args.artifact),
        load_mode=args.load_mode,
        workload_start=args.workload_start,
        periodic_guest=args.periodic_guest,
        dump_chunk_rows=args.dump_chunk_rows,
    )


def write_metadata(
    config: RunConfig,
    status: str,
    *,
    error: str | None = None,
    elapsed_seconds: float | None = None,
) -> None:
    config.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"status={status}",
        f"scheduler={config.scheduler}",
        f"model_mode={config.model_mode}",
        f"load_mode={config.load_mode}",
        f"workload_start={config.workload_start}",
        f"runtime_seconds={config.runtime_seconds}",
        f"expected_inferences={config.expected_inferences}",
        f"expected_samples={config.expected_samples}",
        f"period_ms={config.period_ms}",
        f"periodic_guest={config.periodic_guest}",
        f"dump_chunk_rows={config.dump_chunk_rows}",
        f"periodic_timeout_seconds={config.periodic_timeout_seconds}",
    ]
    if elapsed_seconds is not None:
        lines.append(f"elapsed_seconds={elapsed_seconds:.3f}")
    if error is not None:
        lines.append(f"error={error}")
    for artifact in config.artifacts:
        lines.append(
            f"artifact_sha256={sha256(artifact)}  {artifact.resolve()} size={artifact.stat().st_size}"
        )
    config.metadata_path.write_text("\n".join(lines) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
