#!/usr/bin/env python3
"""Drive one physical-board Task 1 YOLO/periodic arm over the sole UART."""

import argparse
import re
import sys
import time
from pathlib import Path

import serial


class Console:
    def __init__(self, port: str, baud: int, log_path: Path) -> None:
        self.serial = serial.Serial(port, baudrate=baud, timeout=0.1)
        self.log = log_path.open("wb")
        self.buffer = bytearray()

    def close(self) -> None:
        self.serial.close()
        self.log.close()

    def drain(self, seconds: float = 0.2) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = self.serial.read(65536)
            if not chunk:
                continue
            self.log.write(chunk)
            self.log.flush()
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            self.buffer.extend(chunk)
            if len(self.buffer) > 2_000_000:
                del self.buffer[:-1_000_000]

    def expect(self, expression: bytes, timeout: float) -> None:
        pattern = re.compile(expression, re.DOTALL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.drain(0.2)
            if pattern.search(self.buffer):
                return
        raise TimeoutError(f"timeout waiting for {expression!r}")

    def clear_match_window(self) -> None:
        self.buffer.clear()

    def raw(self, data: bytes) -> None:
        self.serial.write(data)
        self.serial.flush()

    def command(self, command: str, prompt: bytes, timeout: float = 30) -> None:
        self.clear_match_window()
        for byte in command.encode() + b"\r":
            self.raw(bytes((byte,)))
            time.sleep(0.002)
        self.expect(prompt, timeout)

    def detach(self) -> None:
        self.clear_match_window()
        self.raw(b"\x18h")
        self.expect(rb"axvisor:/\$", 10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=1_500_000)
    args = parser.parse_args()

    console = Console(args.port, args.baud, Path(args.log))
    guest_prompt = rb"root@starry:[^\r\n]*#"
    host_prompt = rb"axvisor:/\$"
    try:
        console.raw(b"\r")
        console.expect(guest_prompt, 10)
        console.command(
            "wc -c /proc/initrd; sha256sum /proc/initrd; "
            "mkdir -p /tmp/t1; cd /tmp/t1; "
            "gzip -dc /proc/initrd | cpio -id; "
            "mount --bind /tmp/t1/usr/share /usr/share; "
            "ip addr add 10.0.42.15/24 dev eth0 2>/dev/null || true; "
            "echo TASK1_SETUP_DONE",
            guest_prompt,
            90,
        )
        console.clear_match_window()
        for byte in b"/tmp/t1/bin/task2-net model-only\r":
            console.raw(bytes((byte,)))
            time.sleep(0.002)
        console.expect(
            rb"TASK3_MODEL_READY model=yolo11n\.ncnn runtime=ncnn", 30
        )
        console.expect(rb"TASK3_INFER_STARTED", 30)

        console.detach()
        console.command("vm list", host_prompt)
        console.command("vm console 2", rb"Attached VM\[2\] console", 30)
        console.clear_match_window()
        console.raw(b"g")
        console.expect(rb"PERIODIC LATENCY START", 10)
        console.expect(rb"PERIODIC LATENCY COMPLETE samples=300", 90)

        console.detach()
        console.command("rt stat", host_prompt, 30)
        console.command("vmexit stat", host_prompt, 30)
        console.command("vm list", host_prompt, 30)
        console.command("vm console 1", rb"TASK3_INFER .*model=yolo11n\.ncnn", 90)
        console.detach()
        console.drain(1)
    except Exception as error:
        console.drain(2)
        print(f"\nTASK1_ARM_ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        console.close()
    print("\nTASK1_ARM_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
