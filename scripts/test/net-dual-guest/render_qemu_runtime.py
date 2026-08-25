#!/usr/bin/env python3
"""Render per-run rootfs and socket paths into an evidence QEMU config."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


def _replace_toml_string(text: str, old: str, new: str, field: str) -> str:
    old_literal = json.dumps(old)
    new_literal = json.dumps(new)
    if text.count(old_literal) != 1:
        raise ValueError(f"expected exactly one {field} argument, found {text.count(old_literal)}")
    return text.replace(old_literal, new_literal, 1)


def _option_value(args: list[str], option: str) -> str:
    positions = [index for index, value in enumerate(args) if value == option]
    if len(positions) != 1 or positions[0] + 1 >= len(args):
        raise ValueError(f"expected exactly one {option} option")
    return args[positions[0] + 1]


def _unix_socket_argument(original: str, socket_path: Path, option: str) -> str:
    if not original.startswith("unix:"):
        raise ValueError(f"{option} must use a Unix-domain socket")
    suffix = original.partition(",")[2]
    return f"unix:{socket_path}" + (f",{suffix}" if suffix else "")


def _rootfs_argument(args: list[str], rootfs_path: Path) -> str:
    candidates = [
        value
        for value in args
        if value.startswith("id=disk0,") and re.search(r"(?:^|,)file=[^,]+", value)
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one disk0 rootfs argument, found {len(candidates)}")
    return re.sub(r"(?<=file=)[^,]+", str(rootfs_path), candidates[0], count=1)


def _capture_arguments(args: list[str], capture_prefix: Path) -> list[tuple[str, str]]:
    captures = [
        value
        for value in args
        if value.startswith("filter-dump,") and re.search(r"(?:^|,)file=[^,]+", value)
    ]
    if not captures:
        raise ValueError("QEMU config has no filter-dump capture arguments")
    return [
        (
            original,
            re.sub(
                r"(?<=file=)[^,]+",
                f"{capture_prefix}.vm{index}.pcap",
                original,
                count=1,
            ),
        )
        for index, original in enumerate(captures, 1)
    ]


def render_qemu_runtime(
    source_path: Path,
    output_path: Path,
    rootfs_path: Path,
    serial_socket: Path | None,
    qmp_socket: Path,
    capture_prefix: Path | None = None,
    netdev_ports: dict[int, int] | None = None,
    timeout: int | None = None,
) -> None:
    paths = tuple(
        path
        for path in (rootfs_path, serial_socket, qmp_socket, capture_prefix)
        if path is not None
    )
    if any(not path.is_absolute() for path in paths):
        raise ValueError("runtime rootfs and socket paths must be absolute")
    if serial_socket is not None and serial_socket == qmp_socket:
        raise ValueError("serial and QMP sockets must be distinct")
    if any("," in str(path) or "\n" in str(path) for path in paths):
        raise ValueError("runtime paths must not contain commas or newlines")

    source_text = source_path.read_text()
    config = tomllib.loads(source_text)
    args = config.get("args")
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise ValueError(f"QEMU config has no string args array: {source_path}")

    original_qmp = _option_value(args, "-qmp")
    original_rootfs = next(
        (
            value
            for value in args
            if value.startswith("id=disk0,") and re.search(r"(?:^|,)file=[^,]+", value)
        ),
        None,
    )
    if original_rootfs is None:
        raise ValueError("QEMU config has no disk0 rootfs argument")

    rendered = source_text
    if serial_socket is not None:
        original_serial = _option_value(args, "-serial")
        rendered = _replace_toml_string(
            rendered,
            original_serial,
            _unix_socket_argument(original_serial, serial_socket, "-serial"),
            "serial",
        )
    rendered = _replace_toml_string(
        rendered,
        original_qmp,
        _unix_socket_argument(original_qmp, qmp_socket, "-qmp"),
        "QMP",
    )
    rendered = _replace_toml_string(
        rendered,
        original_rootfs,
        _rootfs_argument(args, rootfs_path),
        "rootfs",
    )
    if capture_prefix is not None:
        for index, (original, replacement) in enumerate(
            _capture_arguments(args, capture_prefix), 1
        ):
            rendered = _replace_toml_string(
                rendered, original, replacement, f"capture {index}"
            )
    for old_port, new_port in (netdev_ports or {}).items():
        if not 1 <= old_port <= 65535 or not 1 <= new_port <= 65535:
            raise ValueError("netdev ports must be in 1..65535")
        replacements = 0
        for original in args:
            old_endpoint = f"127.0.0.1:{old_port}"
            if old_endpoint not in original:
                continue
            replacement = original.replace(old_endpoint, f"127.0.0.1:{new_port}")
            rendered = _replace_toml_string(
                rendered, original, replacement, f"netdev port {old_port}"
            )
            replacements += 1
        if replacements == 0:
            raise ValueError(f"QEMU config does not use netdev port {old_port}")
    if timeout is not None:
        if timeout <= 0:
            raise ValueError("QEMU timeout must be positive")
        rendered, replacements = re.subn(
            r"(?m)^timeout\s*=\s*\d+\s*$", f"timeout = {timeout}", rendered
        )
        if replacements != 1:
            raise ValueError(
                f"expected exactly one QEMU timeout field, found {replacements}"
            )
    tomllib.loads(rendered)
    output_path.write_text(rendered)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--serial-socket", type=Path)
    parser.add_argument("--qmp-socket", required=True, type=Path)
    parser.add_argument("--capture-prefix", type=Path)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--netdev-port", action="append", default=[], metavar="OLD=NEW")
    args = parser.parse_args()
    netdev_ports = {}
    for mapping in args.netdev_port:
        old, separator, new = mapping.partition("=")
        if not separator:
            parser.error("--netdev-port must use OLD=NEW")
        try:
            netdev_ports[int(old)] = int(new)
        except ValueError:
            parser.error("--netdev-port values must be integers")
    render_qemu_runtime(
        args.source,
        args.output,
        args.rootfs.resolve(),
        args.serial_socket.resolve() if args.serial_socket is not None else None,
        args.qmp_socket.resolve(),
        args.capture_prefix.resolve() if args.capture_prefix is not None else None,
        netdev_ports,
        args.timeout,
    )


if __name__ == "__main__":
    main()
