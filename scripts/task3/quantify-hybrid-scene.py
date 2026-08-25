#!/usr/bin/env python3
"""Strictly quantify the final fixed-perception versus RKNN scene logs."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
VM1_PREFIX = re.compile(rb"(?m)^\[VM 1\] ?")
CONTROL = re.compile(
    rb"TASK3_CONTROL_SENT elapsed_ms=(\d+) event_index=(\d+) event_id=([^ ]+) source=([^ ]+) "
    rb"generation=(\d+) request=(\d+) action=([^ ]+) value=(-?\d+) outcome=([^ ]+) "
    rb"infer_start_ns=(\d+) infer_end_ns=(\d+) seq=(\d+)"
)
STATUS = re.compile(
    rb"TASK3_STATUS_RECEIVED elapsed_ms=(\d+) event_index=(\d+) event_id=([^ ]+) request=(\d+) "
    rb"value=(-?\d+) state=(-?\d+) protocol_state=([^ ]+) rtt_ms=(\d+) status_ns=(\d+) "
    rb"end_to_end_us=(\d+)"
)
DETECTION = re.compile(
    rb"TASK3_DETECTION event_index=(\d+) event_id=([^ ]+) class=(\d+) confidence_milli=(\d+) "
    rb"center_x_milli=(\d+) center_y_milli=(\d+) area_milli=(\d+) request=(\d+)"
)
COMPLETION = re.compile(
    rb"(?m)^TASK3_EXPERIMENT_COMPLETE source=([^ ]+) events=(\d+) statuses=(\d+) "
    rb"elapsed_ms=(\d+)$"
)
IDS = [
    "road-0375", "road-0380", "road-0385", "road-0390", "road-0395", "road-0400",
    "hazard-0000", "hazard-0010", "hazard-0020", "road-0405", "explicit-reset", "road-0410",
]
TRUTH = {1: 545, 2: 515, 3: 480, 4: 440, 5: 405, 6: 365, 10: 330, 12: 300}
EXPECTED = {
    1: "SetOutput", 2: "SetOutput", 3: "SetOutput", 4: "SetOutput", 5: "SetOutput",
    6: "SetOutput", 7: "Stop", 8: "Stop", 9: "Stop", 10: "Stop", 11: "Reset",
    12: "SetOutput",
}
RKNn_OUTCOMES = {
    1: "track", 2: "track", 3: "track", 4: "track", 5: "track", 6: "track",
    7: "stop-hazard", 8: "stop-latched", 9: "stop-latched", 10: "stop-latched",
    11: "reset", 12: "track",
}
EXPECTED_LOG_SOURCE = {"fixed": "fixed-perception", "rknn": "yolov8.rknn"}
EXPECTED_SCENE_SOURCE = {"fixed": "fixed-perception", "rknn": "rknn"}


def clean(path: Path) -> bytes:
    text = ANSI.sub(b"", path.read_bytes()).replace(b"\r", b"")
    return VM1_PREFIX.sub(b"", text)


def latest_complete_scene(text: bytes, source: str) -> bytes:
    scene_source = EXPECTED_SCENE_SOURCE[source].encode()
    begin = re.compile(
        rb"(?m)^TASK3_HYBRID_SCENE_BEGIN source="
        + re.escape(scene_source)
        + rb" [^\n]+$"
    )
    begins = list(begin.finditer(text))
    if not begins:
        raise ValueError(f"{source}: missing scene begin marker")
    start = begins[-1].start()
    if source == "rknn":
        end_suffix = rb" controller_complete=1 producer_rc=0$"
    else:
        end_suffix = rb" controller_rc=0 producer_rc=na$"
    end = re.compile(
        rb"(?m)^TASK3_HYBRID_SCENE_END source="
        + re.escape(scene_source)
        + end_suffix
    ).search(text, begins[-1].end())
    if end is None:
        raise ValueError(f"{source}: missing successful scene end marker")
    return text[start : end.end()]


def expected_control(source: str, index: int) -> tuple[str, int, str, int]:
    if source == "fixed":
        if index == 11:
            return "Reset", 0, "reset", index
        return "SetOutput", 500, "fixed", index
    action = EXPECTED[index]
    value = 0 if action in {"Stop", "Reset"} else -1
    generation = index if index <= 10 else index - 1
    return action, value, RKNn_OUTCOMES[index], generation


def parse(path: Path, source: str) -> dict:
    if source not in EXPECTED_LOG_SOURCE:
        raise ValueError(f"unsupported source label: {source}")
    text = latest_complete_scene(clean(path), source)
    controls = CONTROL.findall(text)
    statuses = STATUS.findall(text)
    detections = DETECTION.findall(text)
    if len(controls) != 12 or len(statuses) != 12:
        raise ValueError(f"{source}: expected 12 controls/statuses, got {len(controls)}/{len(statuses)}")
    completions = COMPLETION.findall(text)
    expected_log_source = EXPECTED_LOG_SOURCE[source]
    if len(completions) != 1:
        raise ValueError(f"{source}: expected one exact completion marker, got {len(completions)}")
    completion_source, completion_events, completion_statuses, _elapsed_ms = completions[0]
    if (
        completion_source.decode() != expected_log_source
        or int(completion_events) != 12
        or int(completion_statuses) != 12
    ):
        raise ValueError(f"{source}: completion marker does not describe this 12-event run")

    control_rows = []
    status_rows = []
    for expected_index, row in enumerate(controls, 1):
        index = int(row[1])
        event_id = row[2].decode()
        if index != expected_index or event_id != IDS[index - 1]:
            raise ValueError(f"{source}: control sequence mismatch at {expected_index}")
        logged_source = row[3].decode()
        generation = int(row[4])
        request = int(row[5])
        action = row[6].decode()
        value = int(row[7])
        outcome = row[8].decode()
        infer_start_ns = int(row[9])
        infer_end_ns = int(row[10])
        sequence = int(row[11])
        expected_action, expected_value, expected_outcome, expected_generation = expected_control(
            source, index
        )
        if logged_source != expected_log_source:
            raise ValueError(f"{source}: event {index} logged source {logged_source}")
        if request != index or sequence != index:
            raise ValueError(f"{source}: event {index} request/sequence identity mismatch")
        if generation != expected_generation:
            raise ValueError(f"{source}: event {index} generation mismatch")
        if action != expected_action or outcome != expected_outcome:
            raise ValueError(f"{source}: event {index} action/outcome mismatch")
        if expected_value >= 0 and value != expected_value:
            raise ValueError(f"{source}: event {index} control value mismatch")
        if expected_value < 0 and not 0 <= value <= 1000:
            raise ValueError(f"{source}: event {index} control value is out of range")
        if infer_end_ns < infer_start_ns:
            raise ValueError(f"{source}: event {index} inference timestamps moved backwards")
        if source == "fixed" and infer_end_ns != infer_start_ns:
            raise ValueError(f"{source}: fixed perception reported non-zero inference time")
        control_rows.append({
            "index": index,
            "id": event_id,
            "request": request,
            "action": action,
            "value": value,
            "outcome": outcome,
            "infer_start_ns": infer_start_ns,
            "infer_us": (infer_end_ns - infer_start_ns) / 1000,
        })
    for expected_index, row in enumerate(statuses, 1):
        index = int(row[1])
        event_id = row[2].decode()
        if index != expected_index or event_id != IDS[index - 1]:
            raise ValueError(f"{source}: STATUS sequence mismatch at {expected_index}")
        control = control_rows[index - 1]
        request = int(row[3])
        value = int(row[4])
        state = int(row[5])
        protocol_state = row[6].decode()
        status_ns = int(row[8])
        end_to_end_us = int(row[9])
        expected_protocol_state = "Stopped" if control["action"] == "Stop" else "Active"
        if request != control["request"]:
            raise ValueError(f"{source}: event {index} STATUS request does not match CONTROL")
        if value != state or not 0 <= state <= 1000:
            raise ValueError(f"{source}: event {index} STATUS value/state mismatch")
        if protocol_state != expected_protocol_state:
            raise ValueError(f"{source}: event {index} STATUS state does not match action")
        if index > 1 and control["action"] in {"Stop", "Reset"}:
            if state != status_rows[-1]["state"]:
                raise ValueError(f"{source}: event {index} non-updating action changed plant state")
        if status_ns < control["infer_start_ns"]:
            raise ValueError(f"{source}: event {index} STATUS predates inference")
        expected_end_to_end_us = (status_ns - control["infer_start_ns"]) // 1_000
        if end_to_end_us != expected_end_to_end_us:
            raise ValueError(f"{source}: event {index} end-to-end duration is inconsistent")
        status_rows.append({
            "index": index,
            "id": event_id,
            "request": request,
            "state": state,
            "rtt_ms": int(row[7]),
            "end_to_end_us": end_to_end_us,
        })

    detection_rows = {}
    for row in detections:
        index = int(row[0])
        event_id = row[1].decode()
        request = int(row[7])
        if source != "rknn":
            raise ValueError(f"{source}: fixed perception log contains a model detection")
        if index == 11 or not 1 <= index <= 12 or index in detection_rows:
            raise ValueError(f"{source}: invalid or duplicate detection event {index}")
        control = control_rows[index - 1]
        if event_id != control["id"] or request != control["request"]:
            raise ValueError(f"{source}: event {index} detection does not match CONTROL")
        confidence = int(row[3])
        center_x = int(row[4])
        center_y = int(row[5])
        area = int(row[6])
        if not all(0 <= value <= 1000 for value in (confidence, center_x, center_y, area)):
            raise ValueError(f"{source}: event {index} detection field is out of range")
        detection_rows[index] = row

    correct = sum(row["action"] == EXPECTED[row["index"]] for row in control_rows)
    vehicle_hits = sum(index in detection_rows and int(detection_rows[index][2]) in {2, 5, 7} for index in TRUTH)
    hazard_hits = sum(index in detection_rows and int(detection_rows[index][2]) == 0 for index in (7, 8, 9))
    center_errors = [
        abs((int(detection_rows[index][4]) if index in detection_rows else 500) - truth)
        for index, truth in TRUTH.items()
    ]
    return {
        "source": source,
        "events": 12,
        "statuses": 12,
        "correct_decisions": correct,
        "decision_accuracy": correct / 12,
        "vehicle_recall": vehicle_hits / len(TRUTH) if source == "rknn" else 0.0,
        "hazard_recall": hazard_hits / 3 if source == "rknn" else 0.0,
        "center_x_mae_milli": statistics.fmean(center_errors),
        "mean_rtt_ms": statistics.fmean(row["rtt_ms"] for row in status_rows),
        "p95_rtt_ms": sorted(row["rtt_ms"] for row in status_rows)[-2],
        "mean_end_to_end_ms": statistics.fmean(row["end_to_end_us"] for row in status_rows) / 1000,
        "mean_inference_ms": statistics.fmean(
            row["infer_us"] for row in control_rows if row["infer_us"] > 0
        ) / 1000 if source == "rknn" else 0.0,
        "pre_hazard_state": status_rows[5]["state"],
        "hazard_states": [status_rows[index - 1]["state"] for index in (7, 8, 9, 10)],
        "post_reset_state": status_rows[11]["state"],
        "trace": [control | status_rows[i] for i, control in enumerate(control_rows)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixed", required=True, type=Path)
    parser.add_argument("--rknn", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = {"fixed": parse(args.fixed, "fixed"), "rknn": parse(args.rknn, "rknn")}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    fixed, rknn = result["fixed"], result["rknn"]
    report = f"""# Task 3 final hybrid physical-board A/B

| Metric | Fixed perception | YOLOv8 RKNN/NPU |
|---|---:|---:|
| Complete CONTROL→STATUS chains | 12/12 | 12/12 |
| Correct scene decisions | {fixed['correct_decisions']}/12 ({fixed['decision_accuracy']:.1%}) | {rknn['correct_decisions']}/12 ({rknn['decision_accuracy']:.1%}) |
| Vehicle recall | N/A (no detector) | {rknn['vehicle_recall']:.1%} |
| Hazard recall | 0.0% | {rknn['hazard_recall']:.1%} |
| Mean CONTROL→STATUS RTT | {fixed['mean_rtt_ms']:.1f} ms | {rknn['mean_rtt_ms']:.1f} ms |
| Mean inference-start→STATUS | {fixed['mean_end_to_end_ms']:.1f} ms | {rknn['mean_end_to_end_ms']:.1f} ms |
| Mean RKNN inference | N/A | {rknn['mean_inference_ms']:.1f} ms |

Fixed perception continued `SetOutput 500` through all hazard frames. RKNN detected the first
hazard and sent Stop, then kept Stop latched for two further hazard frames and a later safe road
frame. Zephyr state fell from {rknn['pre_hazard_state']} to {' → '.join(map(str, rknn['hazard_states']))}.
The explicit Reset was acknowledged and the final road frame resumed SetOutput.

Both arms used the same FP-RR hybrid topology, Zephyr binary, T2N1 protocol and 12-event input
manifest. The only A/B factor was the perception decision source. All timing timestamps were
captured from StarryOS CLOCK_MONOTONIC; file publication/polling, guest scheduling and network
delivery are included in end-to-end latency.
"""
    (args.out / "REPORT.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
