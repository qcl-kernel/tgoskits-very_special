#!/usr/bin/env python3
"""Render per-run kernel or ramdisk paths into an AxVisor VM config."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


def _replace_path(text: str, field: str, path: Path) -> str:
    pattern = re.compile(rf'(?m)^{re.escape(field)}\s*=\s*"[^"]+"\s*$')
    rendered, replacements = pattern.subn(
        f"{field} = {json.dumps(str(path.resolve()))}", text
    )
    if replacements != 1:
        raise ValueError(f"expected exactly one {field}, found {replacements}")
    return rendered


def render_vm_runtime(
    source_path: Path,
    output_path: Path,
    kernel_path: Path | None = None,
    ramdisk_path: Path | None = None,
) -> None:
    if kernel_path is None and ramdisk_path is None:
        raise ValueError("at least one runtime VM path is required")
    rendered = source_path.read_text()
    if kernel_path is not None:
        rendered = _replace_path(rendered, "kernel_path", kernel_path)
    if ramdisk_path is not None:
        rendered = _replace_path(rendered, "ramdisk_path", ramdisk_path)
    tomllib.loads(rendered)
    output_path.write_text(rendered)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--kernel-path", type=Path)
    parser.add_argument("--ramdisk-path", type=Path)
    args = parser.parse_args()
    render_vm_runtime(
        args.source,
        args.output,
        args.kernel_path,
        args.ramdisk_path,
    )


if __name__ == "__main__":
    main()
