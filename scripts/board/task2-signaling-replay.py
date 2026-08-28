#!/usr/bin/env python3
"""Replay Task-2 T2N1 signaling from a recorded board console.

On the physical board the AxVisor host shell and both guest consoles are
multiplexed onto a single UART, so a two-pane sender/receiver view has to be
built on the host by splitting the one captured console stream.  This tool
reads a recorded console log (raw serial capture, ANSI tolerated), classifies
every T2N1 signaling line by side (sender = StarryOS controller, receiver =
Zephyr/RT-Thread managed) and direction (TX/RX), and writes three plain-text
feeds that tmux panes tail:

    sender.feed     lines emitted by the sender endpoint
    receiver.feed   lines emitted by the receiver endpoint
    status.feed     shared link events (heartbeat stream, SAFE, recovery)

Current official builds split cleanly by marker family (STARRY_T2N1_* versus
TASK2_*).  The legacy FIFO build printed TASK2_* on both sides; those lines
are routed with a sequence state machine (best effort, documented below).

Line format written to every feed:

    TAG [mm:ss.ms] message

TAG is role-relative so the two endpoints read differently: the sender's
outbound is a request and the receiver's outbound is a reply.

    sender   REQ→ 发出 CONTROL      ANS← 收到 ACK/STATUS
    receiver REQ← 收到 CONTROL      ANS→ 回发 ACK/STATUS
    ST  角色当前状态 ·  EV  事件（黄） ·  HB  心跳（灰）

Colors: REQ→/ANS→ green, ANS←/REQ← cyan, ST bold white, EV yellow, HB gray.

Usage:
    task2-signaling-replay.py <log>... [--out-dir DIR] [--speed N]
                               [--heartbeats=show|collapse|hide]
                               [--context=on|brief|off]
                               [--loop[=N]] [--list] [--ui]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    r"|\x1b\[[0-9;?]*[a-zA-Z]"            # CSI
    r"|\x1b[()#][0-9a-zA-Z]"              # two-byte escape
    r"|\x1b."                             # lone escape
)

# Long model/plant context lines that bury the CONTROL/ACK/STATUS exchange.
CONTEXT_EV = (
    "TASK3_MODEL_READY", "TASK3_MODEL_POLICY", "TASK3_INFER", "TASK3_DETECTION",
    "TASK3_RKNN", "TASK3_PLANT_STATE", "TASK3_CONTROL_APPLIED",
)
IMPORTANT_EV = (
    "SAFE", "RECOVER", "RETRY", "FAULT", "READY", "PASS", "MAIN_START",
    "SOCKET", "NET_CONFIGURED", "DISTURBANCE", "PROTOCOL_ERROR", "REJECTED",
    "REMOTE_ERROR", "SESSION_MISMATCH", "DUPLICATE",
)

# New-format markers, separated by side.
SENDER_MARKERS = (
    "STARRY_T2N1_",
    "TASK3_MODEL_READY", "TASK3_MODEL_PROBE", "TASK3_INFER", "TASK3_DETECTION",
    "TASK3_RKNN",
)
RECEIVER_MARKERS = (
    "TASK2_MAIN_START", "TASK2_SOCKET", "TASK2_NET_CONFIGURED", "TASK2_FAULT",
    "TASK2_CONTROL_RECEIVED", "TASK2_STATUS_SENT",
    "TASK3_CONTROL_APPLIED", "TASK3_PLANT_STATE", "TASK3_DISTURBANCE",
)
# Old-format markers where the sender (controller) also used TASK2_*.
CONTROLLER_OLD_MARKERS = (
    "TASK2_READY role=controller",
    "TASK2_BENCHMARK_", "TASK2_P1_PROBE",
    "TASK3_CONTROL_SENT", "TASK3_STATUS_RECEIVED",
    "TASK2_NET_STATS",
)

# Markers printed by both sides in the legacy format; routed by the [VM n]
# prefix when present, else shared to both feeds.
SHARED_EV = (
    "TASK2_SAFE", "TASK2_RECOVERED", "TASK2_DUPLICATE_ACK", "TASK2_DUPLICATE",
    "TASK2_PROTOCOL_ERROR", "TASK2_REMOTE_ERROR", "TASK2_REJECTED",
    "TASK2_SESSION_MISMATCH",
)

SEQUENCE_RE = re.compile(r"\bseq=(\d+)")
REQUEST_RE = re.compile(r"\brequest=(\d+)")
ELAPSED_RE = re.compile(r"\belapsed_ms=(\d+)")


@dataclass
class ClassifyState:
    """Sequence state machine for the legacy dual-TASK2_ logs."""

    last_control_seq: int | None = None
    last_control_when: float = -1.0
    last_status_seq: int | None = None
    last_status_when: float = -1.0


@dataclass
class Feed:
    path: Path
    handle: object

    def write(self, tag: str, elapsed: float, message: str) -> None:
        self.handle.write(f"{tag} [{elapsed:07.3f}] {message}\n")
        self.handle.flush()


@dataclass
class Stats:
    sender_req: int = 0
    sender_ans: int = 0
    sender_ev: int = 0
    receiver_req: int = 0
    receiver_ans: int = 0
    receiver_ev: int = 0
    heartbeat: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"sender   REQ={self.sender_req} ANS={self.sender_ans} EV={self.sender_ev}\n"
            f"receiver REQ={self.receiver_req} ANS={self.receiver_ans} EV={self.receiver_ev}\n"
            f"heartbeat={self.heartbeat} skipped={self.skipped}"
        )


@dataclass
class RoleState:
    """Current protocol state of each endpoint, surfaced as ST lines."""

    sender: str = "等待首个请求"
    receiver: str = "等待首个请求"


def side_tag(side: str, direction: str, message: str = "") -> str:
    """Role-relative tag so the two endpoints read differently.

    The same wire message carries opposite semantics per side: the sender's
    outbound is a REQuest, the receiver's outbound is an ANSwer.  An ACK is
    tagged ACK← on both sides because it confirms the counterpart's last frame.

    sender   REQ→ 发出 CONTROL     ANS← 收到 STATUS   ACK← 收到确认
    receiver REQ← 收到 CONTROL     ANS→ 回发 STATUS   ACK← 收到确认
    """
    if "ACK" in message:
        return "ACK←"
    if direction == "TX":
        return "REQ→" if side == "sender" else "ANS→"
    if direction == "RX":
        return "ANS←" if side == "sender" else "REQ←"
    return direction  # EV / HB / ST


def derive_state(side: str, direction: str, message: str) -> str | None:
    """New endpoint state after this line, or None if unchanged."""
    if side == "both":
        if "SAFE" in message:
            return "链路异常 Safe · 等待恢复"
        if "RECOVER" in message:
            return "已恢复 · 重启可靠流"
        return None
    if side == "sender":
        if direction == "TX":
            return f"发起请求 seq={_seq(message)} · 等待应答"
        if direction == "RX":
            if "STATUS" in message:
                request = REQUEST_RE.search(message)
                seq = _seq(message) or (request.group(1) if request else None)
                return f"收到 STATUS · 事务 {seq} 完成"
            return f"收到 ACK seq={_seq(message)} · 等待 STATUS"
        if "SAFE" in message:
            return "链路异常 Safe · 等待恢复"
        if "RECOVER" in message:
            return "已恢复 · 重启可靠流"
        return None
    # receiver
    if direction == "RX":
        if "ACK" in message:
            return "应答已被确认 · 等待下一请求"
        request = REQUEST_RE.search(message)
        if request:
            return f"收到请求 #{request.group(1)} · 执行中"
        return "收到请求 · 执行中"
    if direction == "TX":
        return f"已应答 STATUS seq={_seq(message)} · 等待下一请求"
    if "SAFE" in message:
        return "链路异常 Safe · 等待恢复"
    if "RECOVER" in message:
        return "已恢复 · 重启可靠流"
    return None


SUMMARY_FIELDS = (("seq", "seq"), ("request", "request"), ("value", "value"),
                  ("attempt", "attempt"), ("rtt_ms", "rtt"), ("bytes", "bytes"))


def summarize(side: str, direction: str, message: str) -> str | None:
    """Reformat a TX/RX line so the peer role is named explicitly.

    The sender's lines say "CONTROL →接收方", the receiver's lines say
    "CONTROL ←发送方", so a glance at any line reveals which side this pane
    is.  Returns None for lines that stay raw (EV / unknown).
    """
    if direction not in ("TX", "RX"):
        return None
    if "RETRANSMIT" in message:
        kind = "重发"
    elif "ACK" in message:
        kind = "ACK"
    elif "STATUS" in message:
        kind = "STATUS"
    elif "CONTROL" in message:
        kind = "CONTROL"
    elif "HEARTBEAT" in message:
        kind = "心跳"
    else:
        return None
    peer = "接收方" if side == "sender" else "发送方"
    arrow = "→" if direction == "TX" else "←"
    parts = []
    for token, label in SUMMARY_FIELDS:
        match = re.search(rf"\b{token}=(\d+)", message)
        if match:
            parts.append(f"{label}={match.group(1)}")
    suffix = " ".join(parts)
    return f"{kind} {arrow}{peer} {suffix}".rstrip()


def proto_time(message: str) -> int | None:
    """Protocol elapsed time in ms (task2-net uptime), if the log carries one."""
    match = ELAPSED_RE.search(message)
    if match:
        return int(match.group(1))
    match = re.search(r"\bat_ms=(\d+)", message)  # disturbance schedule time
    if match:
        return int(match.group(1))
    return None


def interleave_events(events: list) -> list:
    """Rebuild the logical per-transaction exchange across the two endpoints.

    The board capture batches each VM's console output, so the receiver's
    lines trail the sender's by seconds even though the protocol exchanged
    them within one RTT.  This pairs each sender CONTROL (by request id) with
    the receiver's CONTROL_RECEIVED/processing/STATUS/ACK, re-emits them in
    the canonical ping-pong order, and stamps every event with the protocol
    elapsed time so the before/after and the RTT are visible:

        sender REQ→ CONTROL (t0) ──► receiver REQ← CONTROL (t0+1)
        receiver EV 处理 (t0+2..) ──► receiver ANS→ STATUS (t0+rtt+1)
        sender ACK← (t0+rtt+2) ──► sender ANS← STATUS (t0+rtt+3)
        ──► receiver ACK← (t0+rtt+4)

    Events are then stably sorted by that timestamp.  Unmatched events
    (retransmits, SAFE, recovery, setup) keep their own elapsed_ms / raw
    order.
    """
    sender_events = [e for e in events if e[0] == "sender"]
    receiver_events = [e for e in events if e[0] == "receiver"]
    other_events = [e for e in events if e[0] not in ("sender", "receiver")]
    used_s: set[int] = set()
    used_r: set[int] = set()
    out: list = []

    def receiver_request(rev) -> str | None:
        side, direction, message = rev[0], rev[1], rev[2]
        if side == "receiver" and direction == "RX" and "CONTROL" in message:
            match = REQUEST_RE.search(message)
            return match.group(1) if match else None
        return None

    for si, event in enumerate(sender_events):
        if si in used_s:
            continue
        side, direction, message, t_ms = event
        if direction == "TX" and "CONTROL" in message:
            req_match = REQUEST_RE.search(message)
            req = req_match.group(1) if req_match else None
            ri_match = next(
                (ri for ri, rev in enumerate(receiver_events)
                 if ri not in used_r and receiver_request(rev) == req),
                None,
            )
            if ri_match is not None:
                t0 = proto_time(message) or 0
                rtt = 0
                for k in range(si + 1, len(sender_events)):
                    if k in used_s:
                        continue
                    if sender_events[k][1] == "RX" and "STATUS" in sender_events[k][2]:
                        rtt_match = re.search(r"\brtt_ms=(\d+)", sender_events[k][2])
                        rtt = int(rtt_match.group(1)) if rtt_match else 0
                        break
                t_resp = t0 + rtt
                out.append((side, direction, message, t0))
                step = 0
                j = ri_match
                while j < len(receiver_events):
                    rev = receiver_events[j]
                    rd, rm = rev[1], rev[2]
                    if rd == "RX" and "ACK" in rm:
                        out.append((rev[0], rd, rm, t_resp + 4))
                        used_r.add(j)
                        break
                    if rd == "TX" and "STATUS" in rm:
                        out.append((rev[0], rd, rm, t_resp + 1))
                        used_r.add(j)
                        j += 1
                        continue
                    step += 1
                    out.append((rev[0], rd, rm, t0 + step))
                    used_r.add(j)
                    j += 1
                ack = status = None
                si2 = si + 1
                while si2 < len(sender_events):
                    if si2 in used_s:
                        si2 += 1
                        continue
                    sd, smsg = sender_events[si2][1], sender_events[si2][2]
                    if sd == "RX":
                        if "ACK" in smsg and ack is None:
                            ack = sender_events[si2]
                            used_s.add(si2)
                        elif "STATUS" in smsg and status is None:
                            status = sender_events[si2]
                            used_s.add(si2)
                            break
                    si2 += 1
                if ack:
                    out.append((ack[0], ack[1], ack[2], t_resp + 2))
                if status:
                    out.append((status[0], status[1], status[2], t_resp + 3))
                continue
        out.append((side, direction, message, t_ms))
        used_s.add(si)

    for ri, rev in enumerate(receiver_events):
        if ri not in used_r:
            out.append((rev[0], rev[1], rev[2], proto_time(rev[2])))
    for rev in other_events:
        out.append((rev[0], rev[1], rev[2], proto_time(rev[2])))

    # Stable sort by protocol time; untimed events keep raw order at the end.
    known = [e for e in out if e[3] is not None]
    unknown = [e for e in out if e[3] is None]
    known.sort(key=lambda e: e[3])
    carry = known[-1][3] if known else 0
    for e in unknown:
        carry += 1
        known.append((e[0], e[1], e[2], carry))
    return known


def strip_ansi(data: bytes) -> str:
    text = ANSI_RE.sub("", data.decode("utf-8", "replace"))
    text = text.replace("\r", "\n")
    return text


def iter_signaling_lines(log_paths: list[Path], wanted: set[str]):
    """Yield (raw_line, has_elapsed_ms) for every signaling line in order."""
    for path in log_paths:
        raw = path.read_bytes()
        for line in strip_ansi(raw).split("\n"):
            line = line.strip()
            if not line:
                continue
            if not any(marker in line for marker in wanted):
                continue
            yield line


def vm_hint(line: str) -> str | None:
    """Some old captures prefix each line with [VM 1] / [VM 2]."""
    match = re.match(r"\[VM ([12])\]", line)
    return "sender" if match and match.group(1) == "1" else "receiver" if match else None


def classify(line: str, state: ClassifyState) -> tuple[str, str, str] | None:
    """Return (side, direction, message) or None to skip.

    side in {"sender", "receiver", "both"}, direction in {TX, RX, EV, HB}.
    """
    hint = vm_hint(line)
    if hint:
        line = re.sub(r"^\[VM [12]\]\s*", "", line)

    # New format, exact.
    if "STARRY_T2N1_" in line:
        side = "sender"
        if "STARRY_T2N1_CONTROL_SENT" in line or "STARRY_T2N1_RETRANSMIT" in line:
            direction = "TX"
        elif "STARRY_T2N1_ACK" in line or "STARRY_T2N1_STATUS_DELIVERED" in line:
            direction = "RX"
        else:
            direction = "EV"
        return side, direction, line

    # Controller-side model / infer context.
    if any(marker in line for marker in SENDER_MARKERS):
        return "sender", "EV", line

    # Receiver-side plant context.
    if any(marker in line for marker in RECEIVER_MARKERS):
        if "TASK2_CONTROL_RECEIVED" in line:
            return "receiver", "RX", line
        if "TASK2_STATUS_SENT" in line:
            state.last_status_seq = _seq(line)
            state.last_status_when = time.monotonic()
            return "receiver", "TX", line
        return "receiver", "EV", line

    # Legacy controller markers.
    if any(marker in line for marker in CONTROLLER_OLD_MARKERS):
        if "TASK3_CONTROL_SENT" in line or "TASK2_BENCHMARK_CONTROL_SENT" in line:
            state.last_control_seq, state.last_control_when = _seq(line), time.monotonic()
            return "sender", "TX", line
        if "TASK3_STATUS_RECEIVED" in line or "TASK2_BENCHMARK_STATUS_RECEIVED" in line:
            return "sender", "RX", line
        return "sender", "EV", line

    if "TASK2_HEARTBEAT_RECEIVED" in line:
        return "both", "HB", line

    if "TASK2_READY" in line:
        if "role=controller" in line:
            return "sender", "EV", line
        if "role=managed" in line:
            return "receiver", "EV", line
        # Old build, role unknown: hint decides, else sender by default.
        return (hint or "sender"), "EV", line

    if "TASK2_ACK" in line:
        seq = _seq(line)
        if seq is not None and state.last_status_when > state.last_control_when \
                and seq == state.last_status_seq:
            return "receiver", "RX", line
        return "sender", "RX", line

    if "TASK2_RETRANSMIT" in line:
        seq = _seq(line)
        if seq is not None and state.last_status_when > state.last_control_when \
                and seq == state.last_status_seq:
            return "receiver", "TX", line
        return "sender", "TX", line

    if any(marker in line for marker in SHARED_EV):
        if "RetryExhausted" in line:
            return "sender", "EV", line
        if "SendFailure" in line:
            return "receiver", "EV", line
        if hint:
            return hint, "EV", line
        # New-format bare TASK2_SAFE is always the Zephyr receiver; the
        # controller's Safe is STARRY_T2N1_SAFE.
        return "receiver", "EV", line

    if hint:
        return hint, "EV", line

    return None


def _seq(line: str) -> int | None:
    match = SEQUENCE_RE.search(line)
    return int(match.group(1)) if match else None


def emit_message(line: str, context: str) -> str | None:
    """Apply --context filtering to EV lines; None drops the line."""
    if any(marker in line for marker in CONTEXT_EV):
        if context == "on":
            return line
        if context == "off":
            return None
        return line if len(line) <= 96 else line[:93] + "…"
    if any(marker in line for marker in IMPORTANT_EV):
        return line
    return line


def draw_ui(sender: list[str], receiver: list[str], status: list[str], total: int) -> None:
    """Render a single-terminal split-screen preview without tmux."""
    width, height = shutil.get_terminal_size((120, 40))
    half = max(10, width // 2)
    title = (f"[SENDER  StarryOS 10.0.42.15:4242]".ljust(half - 1)
             + "│" + f"[RECEIVER  Zephyr 10.0.42.2:4242]".ljust(half - 1))
    rows = max(4, height - 5)
    sender_tail, receiver_tail = sender[-rows:], receiver[-rows:]
    sender_tail += [""] * (rows - len(sender_tail))
    receiver_tail += [""] * (rows - len(receiver_tail))
    def cell_color(text: str) -> str:
        if not text:
            return ""
        head = text[:4]
        if head[:2] in ("EV", "HB", "ST"):
            return {"EV": "\x1b[33m", "HB": "\x1b[90m", "ST": "\x1b[1;97m"}[head[:2]]
        if "→" in head:
            return "\x1b[32m"
        if "←" in head:
            return "\x1b[36m"
        return ""

    frame = ["\x1b[H", "\x1b[1;37;44m " + title[: width - 1] + " \x1b[0m"]
    for left, right in zip(sender_tail, receiver_tail):
        lcell = (cell_color(left) + left + "\x1b[0m")[: half - 1]
        rcell = (cell_color(right) + right + "\x1b[0m")[: half - 1]
        frame.append(lcell.ljust(half - 1) + "│" + rcell)
    status_line = status[-1] if status else "idle"
    frame.append(f"\x1b[90m· {total} signaling events · {status_line}\x1b[0m")
    sys.stdout.write("\n".join(frame))
    sys.stdout.flush()


def paced_delay(elapsed: float | None, last_elapsed: float | None, speed: float) -> float:
    if speed <= 0:
        return 0
    if elapsed is not None and last_elapsed is not None and elapsed >= last_elapsed:
        delay = (elapsed - last_elapsed) / 1000.0 / speed
        return max(0.02, min(3.0, delay))
    return 0.15 / speed


def main() -> int:
    args = parse_arguments()
    wanted = set(SENDER_MARKERS) | set(RECEIVER_MARKERS) | set(CONTROLLER_OLD_MARKERS) | {
        "TASK2_READY", "TASK2_ACK", "TASK2_RETRANSMIT", "TASK2_HEARTBEAT_RECEIVED",
        "TASK2_SAFE", "TASK2_RECOVERED", "TASK2_DUPLICATE", "TASK2_PROTOCOL_ERROR",
        "TASK2_REMOTE_ERROR", "TASK2_REJECTED", "TASK2_SESSION_MISMATCH",
    }

    lines = list(iter_signaling_lines(args.logs, wanted))
    if not lines:
        print("no T2N1 signaling lines found in the given logs", file=sys.stderr)
        return 1

    if args.list:
        stats = Stats()
        state = ClassifyState()
        for line in lines:
            result = classify(line, state)
            if result is None:
                stats.skipped += 1
                continue
            side, direction, _ = result
            if direction == "HB":
                stats.heartbeat += 1
                continue
            _record(stats, side, direction)
        print(stats)
        return 0

    # Phase 1: classify every signaling line into (side, direction, message),
    # keeping the raw capture order so the legacy seq state machine works.
    classify_state = ClassifyState()
    events: list = []
    skipped = 0
    for line in lines:
        result = classify(line, classify_state)
        if result is None:
            skipped += 1
            continue
        side, direction, message = result
        if direction == "EV":
            message = emit_message(message, args.context)
            if message is None:
                skipped += 1
                continue
        events.append((side, direction, message, proto_time(message)))
    # Phase 2: rebuild the logical ping-pong across the two endpoints, since
    # the raw capture batches each VM's console output into bursts.
    if args.interleave:
        events = interleave_events(events)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    feeds = {
        "sender": Feed(out_dir / "sender.feed", (out_dir / "sender.feed").open("w")),
        "receiver": Feed(out_dir / "receiver.feed", (out_dir / "receiver.feed").open("w")),
        "status": Feed(out_dir / "status.feed", (out_dir / "status.feed").open("w")),
    }
    feeds["status"].write(
        "EV", 0.0,
        "链路: 发送方(10.0.42.15:4242) ──请求──▶ 接收方(10.0.42.2:4242)")

    stats = Stats()
    passes = 1 if args.loop is None else args.loop
    cycle = 0
    ui_sender: list[str] = []
    ui_receiver: list[str] = []
    ui_status: list[str] = []
    if args.ui:
        sys.stdout.write("\x1b[2J")

    try:
        while True:
            cycle += 1
            start = time.monotonic()
            last_elapsed = None
            last_hb_flush = 0.0
            hb_pending = 0
            role_state = RoleState()
            if cycle > 1:
                feeds["status"].write("EV", 0.0, f"----- replay cycle {cycle} -----")
                if args.ui:
                    ui_status.append(f"EV [000.000] ----- replay cycle {cycle} -----")
            for side, direction, message, t_ms in events:
                elapsed = t_ms
                delay = paced_delay(elapsed, last_elapsed, args.speed)
                if elapsed is not None:
                    last_elapsed = elapsed
                if delay:
                    time.sleep(delay)
                rel = (elapsed if elapsed is not None else (last_elapsed or 0)) / 1000.0

                if direction == "HB":
                    hb_pending += 1
                    stats.heartbeat += 1
                    if args.heartbeats == "show":
                        feeds["receiver"].write("HB", rel, message)
                    elif args.heartbeats == "collapse" and rel - last_hb_flush >= 1.0:
                        feeds["status"].write("HB", rel,
                                              f"heartbeat stream alive ({hb_pending} frames since last)")
                        if args.ui:
                            ui_status.append(f"HB [{rel:07.3f}] heartbeat stream alive")
                        hb_pending = 0
                        last_hb_flush = rel
                    continue
                _record(stats, side, direction)
                targets = ("sender", "receiver") if side == "both" else (side,)
                for target in targets:
                    display = summarize(target, direction, message) or message
                    feeds[target].write(side_tag(target, direction, message), rel, display)
                    new_state = derive_state(target, direction, message)
                    if new_state and new_state != role_state.__dict__[target]:
                        role_state.__dict__[target] = new_state
                        feeds[target].write("ST", rel, new_state)
                if args.ui:
                    for target in targets:
                        tag = side_tag(target, direction, message)
                        display = summarize(target, direction, message) or message
                        rendered = f"{tag} [{rel:07.3f}] {display}"
                        if target == "sender":
                            ui_sender.append(rendered)
                        else:
                            ui_receiver.append(rendered)
                    draw_ui(ui_sender, ui_receiver, ui_status,
                            sum(vars(stats).values()) - stats.skipped)

            if hb_pending > 0:
                rel = time.monotonic() - start
                feeds["status"].write("HB", rel,
                                      f"heartbeat stream alive ({hb_pending} frames since last)")
                if args.ui:
                    ui_status.append(f"HB [{rel:07.3f}] heartbeat stream alive")

            if passes is not None and passes > 0 and cycle >= passes:
                break
            if args.ui:
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        for feed in feeds.values():
            feed.handle.close()

    print(f"replayed {sum(vars(stats).values()) - stats.skipped} signaling lines -> {out_dir}")
    print(stats)
    if skipped:
        print(f"(collect phase skipped {skipped} lines)")
    return 0


def _record(stats: Stats, side: str, direction: str) -> None:
    key = {"sender": {"TX": "sender_req", "RX": "sender_ans", "EV": "sender_ev"},
           "receiver": {"TX": "receiver_req", "RX": "receiver_ans", "EV": "receiver_ev"}}
    for target in (["sender", "receiver"] if side == "both" else [side]):
        setattr(stats, key[target][direction], getattr(stats, key[target][direction]) + 1)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path,
                        help="recorded console log(s), replayed in order")
    parser.add_argument("--out-dir", default="/tmp/t2n1-demo/feeds",
                        help="directory for sender.feed / receiver.feed / status.feed")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="replay speed multiplier (0 = instant dump)")
    parser.add_argument("--loop", nargs="?", type=int, const=0, default=None,
                        help="repeat the replay: --loop = forever, --loop N = N times")
    parser.add_argument("--heartbeats", choices=("show", "collapse", "hide"),
                        default="collapse",
                        help="how to surface HEARTBEAT_RECEIVED lines (default collapse)")
    parser.add_argument("--context", choices=("on", "brief", "off"), default="brief",
                        help="verbosity of model/plant context EV lines (default brief)")
    parser.add_argument("--interleave", action="store_true", default=True,
                        help="rebuild the logical per-transaction ping-pong "
                             "instead of the raw (console-batched) order")
    parser.add_argument("--no-interleave", dest="interleave", action="store_false",
                        help="preserve the literal capture order (evidence view)")
    parser.add_argument("--list", action="store_true",
                        help="print a classification summary and exit")
    parser.add_argument("--ui", action="store_true",
                        help="render a self-contained split-screen preview instead of feeds")
    args = parser.parse_args()
    for path in args.logs:
        if not path.is_file():
            parser.error(f"log not found: {path}")
    if args.speed < 0:
        parser.error("--speed must be non-negative (0 = instant dump)")
    return args


if __name__ == "__main__":
    raise SystemExit(main())