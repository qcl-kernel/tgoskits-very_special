#!/usr/bin/env python3
"""Record a validated Task 2/3 HTML dashboard as an indexed MP4 replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def executable(explicit: str | None, candidates: tuple[str, ...], purpose: str) -> str:
    if explicit:
        path = shutil.which(explicit) if "/" not in explicit else explicit
        if path and Path(path).is_file():
            return str(Path(path).resolve())
        raise ValueError(f"{purpose} executable is unavailable: {explicit}")
    for candidate in candidates:
        if path := shutil.which(candidate):
            return path
    raise ValueError(f"{purpose} executable is unavailable; set an explicit path")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_frame(
    browser: str,
    dashboard: Path,
    output: Path,
    frame_index: int,
    safe: bool,
) -> None:
    query = "?safe=1" if safe else ""
    # Every browser process starts the evidence replay from a known cursor.
    # Advancing virtual time makes each captured frame deterministic without
    # depending on host wall-clock scheduling.
    virtual_time_ms = 1200 + frame_index * 900
    result = subprocess.run(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            f"--virtual-time-budget={virtual_time_ms}",
            "--window-size=1480,720",
            f"--screenshot={output.resolve()}",
            dashboard.resolve().as_uri() + query,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"browser frame capture failed: {result.stderr.strip()}")


def write_index(output_dir: Path) -> None:
    artifacts = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact-index.json":
            continue
        relative = path.relative_to(output_dir)
        if relative.parts[0] == "raw":
            role = "raw-log"
        elif path.suffix == ".mp4":
            role = "demo-video"
        else:
            role = "rendered-demo"
        artifacts.append(
            {
                "path": str(relative),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "role": role,
            }
        )
    (output_dir / "artifact-index.json").write_text(
        json.dumps({"schema": 1, "artifacts": artifacts}, indent=2) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--safe-frames", type=int, default=0)
    parser.add_argument("--capture-fps", type=int, default=2)
    parser.add_argument("--browser", default=os.environ.get("TASK123_BROWSER"))
    parser.add_argument("--ffmpeg", default=os.environ.get("TASK123_FFMPEG"))
    args = parser.parse_args()
    if args.frames <= 0 or args.capture_fps <= 0:
        parser.error("--frames and --capture-fps must be positive")
    if args.safe_frames < 0 or args.safe_frames > args.frames:
        parser.error("--safe-frames must be between zero and --frames")
    if not args.dashboard.is_file():
        parser.error(f"dashboard is missing: {args.dashboard}")
    if args.out.parent.resolve() != args.dashboard.parent.resolve():
        parser.error("video and dashboard must share one evidence directory")

    try:
        browser = executable(
            args.browser,
            ("google-chrome", "chromium", "chromium-browser"),
            "browser",
        )
        ffmpeg = executable(args.ffmpeg, ("ffmpeg",), "ffmpeg")
    except ValueError as error:
        parser.error(str(error))

    with tempfile.TemporaryDirectory(prefix="task123-demo-frames-") as directory:
        frame_dir = Path(directory)
        normal_frames = args.frames - args.safe_frames
        for index in range(args.frames):
            safe = index >= normal_frames
            replay_index = index - normal_frames if safe else index
            capture_frame(
                browser,
                args.dashboard,
                frame_dir / f"frame-{index:03d}.png",
                replay_index,
                safe,
            )
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-framerate",
                str(args.capture_fps),
                "-i",
                str(frame_dir / "frame-%03d.png"),
                "-vf",
                "fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-movflags",
                "+faststart",
                str(args.out),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not args.out.is_file():
            raise RuntimeError(f"ffmpeg video encoding failed: {result.stderr.strip()}")

    write_index(args.out.parent)
    print(
        f"TASK123_DEMO_VIDEO_PASS output={args.out.resolve()} "
        f"sha256={sha256(args.out)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
