#!/usr/bin/env python3
"""Render the live tmux demo screen (I_I layout) into a crisp PNG.

Screen capture on the board host returns a blank frame when the desktop is
locked, so this reproduces exactly what the tmux panes show — sender (navy)
left, receiver (green) right, full-width link-status strip below — from the
three feeds written by task2-signaling-replay.py.

Usage:
    task2-signaling-shot.py [--feeds DIR] [--out shot.png] [--rows 22] [--scale 2]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# ANSI-256 approximations of the viewer palette.
SENDER_BG = (20, 30, 55)       # 48;5;23 navy
RECEIVER_BG = (12, 48, 30)     # 48;5;22 green
STATUS_BG = (38, 42, 50)
HEADER_SENDER = (38, 72, 128)
HEADER_RECEIVER = (34, 118, 66)
GREEN = (74, 222, 128)
CYAN = (96, 210, 220)
YELLOW = (230, 196, 122)
GRAY = (140, 145, 150)
WHITE = (235, 240, 245)
TITLE_WHITE = (255, 255, 255)

TITLE_SENDER = "发送方 controller · StarryOS 10.0.42.15:4242"
TITLE_RECEIVER = "接收方 managed · Zephyr 10.0.42.2:4242"
TITLE_STATUS = "LINK STATUS · 发送方 ──请求──▶ 接收方"


def tag_color(tag: str):
    if tag.startswith("ST"):
        return WHITE
    if tag == "EV":
        return YELLOW
    if tag == "HB":
        return GRAY
    if tag.endswith("→"):
        return GREEN
    if tag.endswith("←"):
        return CYAN
    return WHITE


def tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()
    return lines[-n:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds", default="/tmp/t2n1-demo/feeds", type=Path)
    parser.add_argument("--out", default="/tmp/t2n1-demo/shot.png", type=Path)
    parser.add_argument("--rows", type=int, default=22)
    parser.add_argument("--scale", type=int, default=2)
    args = parser.parse_args()

    sender = tail(args.feeds / "sender.feed", args.rows)
    receiver = tail(args.feeds / "receiver.feed", args.rows)
    status = tail(args.feeds / "status.feed", 4)

    mono = ImageFont.truetype(MONO, 9 * args.scale)
    cjk = ImageFont.truetype(CJK, 10 * args.scale)
    cell_w = int(mono.getlength("M"))
    cell_h = int(cjk.getbbox("发送")[3]) + 4 * args.scale

    def width(text: str) -> int:
        total = 0
        for ch in text:
            total += cell_w if ord(ch) < 0x2E80 else cell_w * 2
        return total

    rows = max(len(sender), len(receiver))
    col_w = max(width(TITLE_SENDER), *(width(l) for l in sender),
                *(width(l) for l in receiver)) + 3 * args.scale
    header_h = int(1.9 * cell_h)
    img_w = col_w * 2
    img_h = header_h + rows * cell_h + int(1.3 * cell_h) * len(status) + 8 * args.scale

    img = Image.new("RGB", (img_w, img_h), STATUS_BG)
    draw = ImageDraw.Draw(img)

    def draw_line(x: int, y: int, text: str, fill, cjk_font=cjk) -> None:
        xx = x
        for ch in text:
            font = cjk_font if ord(ch) >= 0x2E80 else mono
            draw.text((xx, y), ch, font=font, fill=fill)
            xx += cell_w if ord(ch) < 0x2E80 else cell_w * 2

    def draw_pane(ox: int, bg: tuple, header_bg: tuple, title: str,
                  lines: list[str], rows: int) -> None:
        draw.rectangle((ox, 0, ox + col_w, header_h), fill=header_bg)
        draw_line(ox + 3 * args.scale, header_h // 2 - cell_h // 2, title, TITLE_WHITE)
        for i in range(rows):
            yy = header_h + i * cell_h
            draw.rectangle((ox, yy, ox + col_w, yy + cell_h), fill=bg)
            if i < len(lines):
                line = lines[i]
                tag = line.split(" ", 1)[0]
                draw_line(ox + 3 * args.scale, yy + 1, line, tag_color(tag))

    draw_pane(0, SENDER_BG, HEADER_SENDER, TITLE_SENDER, sender, rows)
    draw_pane(col_w, RECEIVER_BG, HEADER_RECEIVER, TITLE_RECEIVER, receiver, rows)

    yy = img_h - int(1.3 * cell_h) * len(status)
    draw.rectangle((0, yy, img_w, img_h), fill=STATUS_BG)
    draw_line(3 * args.scale, yy + 2, TITLE_STATUS, GRAY)
    for i, line in enumerate(status):
        tag = line.split(" ", 1)[0]
        draw_line(3 * args.scale, yy + int(1.3 * cell_h) * (i + 1), line, tag_color(tag))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.out)
    print(f"shot -> {args.out}  {img_w}x{img_h}  (sender {len(sender)} / receiver {len(receiver)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())