#!/usr/bin/env python3
"""Deterministic tests for StarryOS Task-2/Task-3 evidence checks."""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_starry_task23.py")
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("verify_starry_task23", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


def frame(
    *,
    src: str,
    dst: str,
    kind: int,
    sequence: int = 0,
    acknowledgement: int = 0,
    error_code: int = 0,
    body: bytes = b"",
) -> VERIFY.WireFrame:
    return VERIFY.WireFrame(
        src=src,
        dst=dst,
        kind=kind,
        sequence=sequence,
        acknowledgement=acknowledgement,
        error_code=error_code,
        body=body,
    )


class VerifyStarryTask23Tests(unittest.TestCase):
    def test_task2_scope_accepts_protocol_evidence_without_task3_model_markers(self) -> None:
        frames = []
        for sequence in range(1, 4):
            frames.extend(
                (
                    frame(
                        src=VERIFY.STARRY_IP,
                        dst=VERIFY.ZEPHYR_IP,
                        kind=VERIFY.KIND_CONTROL,
                        sequence=sequence,
                    ),
                    frame(
                        src=VERIFY.ZEPHYR_IP,
                        dst=VERIFY.STARRY_IP,
                        kind=VERIFY.KIND_STATUS,
                        sequence=sequence,
                    ),
                )
            )
        log = "\n".join(
            (
                "TASK2_CONTROLLER_READY mode=task2 source=frozen-control",
                "STARRY_T2N1_PASS",
                "STARRY_T2N1_STATUS_DELIVERED request=3",
            )
        )

        self.assertEqual(VERIFY.verify_task2_normal(frames, log), [])

    def test_retry_exhaustion_requires_five_retries_and_no_ack(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            )
            for _ in range(6)
        ]
        log = "\n".join(
            (
                "TASK2_FAULT_MODE mode=drop-ack-always",
                "TASK2_FAULT_DROP_ACK_ALWAYS seq=1",
                "STARRY_T2N1_RETRANSMIT seq=1 attempt=5",
                "STARRY_T2N1_SAFE source=protocol reason=RetryExhausted",
                "STARRY_T2N1_RECOVERED state=Active",
            )
        )

        self.assertEqual(VERIFY.verify_retry_exhausted(frames, log), [])

    def test_out_of_order_injection_is_proven_by_wire_capture(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=2,
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_ERROR,
                acknowledgement=2,
                error_code=2,
            ),
        ]
        log = "\n".join(
            (
                "TASK2_PROTOCOL_ERROR out_of_order=2 expected=1",
                "STARRY_T2N1_FAULT_RECOVERY_COMPLETE mode=out-of-order "
                "safe_observed=true recovered=true",
                "STARRY_T2N1_PASS",
            )
        )

        self.assertEqual(VERIFY.verify_out_of_order(frames, log), [])

    def test_invalid_parameter_injection_is_proven_by_wire_capture(self) -> None:
        payload = bytearray(12)
        payload[4:8] = (1001).to_bytes(4, "big", signed=True)
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
                body=bytes(payload),
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_ERROR,
                acknowledgement=1,
                error_code=1,
            ),
        ]
        log = "\n".join(
            (
                "TASK2_PROTOCOL_ERROR invalid_parameter seq=1",
                "STARRY_T2N1_FAULT_RECOVERY_COMPLETE mode=invalid-parameter "
                "safe_observed=true recovered=true",
                "STARRY_T2N1_PASS",
            )
        )

        self.assertEqual(VERIFY.verify_invalid_parameter(frames, log), [])

    def test_blackout_requires_both_safe_states_and_completed_recovery_cycle(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            ),
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=2,
            ),
        ]
        incomplete_log = "\n".join(
            (
                "virtnet: blackout ON",
                "STARRY_T2N1_SAFE source=protocol reason=RetryExhausted",
                "virtnet: blackout OFF",
                "STARRY_T2N1_RECOVERED state=Active",
                "STARRY_T2N1_STATUS_DELIVERED request=3",
            )
        )

        self.assertTrue(VERIFY.verify_blackout(frames, incomplete_log))

    def test_blackout_accepts_control_markers_before_and_after_recovery(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_STATUS,
                sequence=1,
            ),
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_STATUS,
                sequence=1,
            ),
        ]
        complete_log = "\n".join(
            (
                "TASK2_CONTROL_RECEIVED seq=1 request=1",
                "virtnet: blackout ON",
                "STARRY_T2N1_SAFE source=protocol reason=RetryExhausted",
                "TASK2_SAFE state=Safe event=HeartbeatTimeout",
                "virtnet: blackout OFF",
                "STARRY_T2N1_RECOVERED state=Active",
                "TASK3_INFER model=yolo11n.ncnn infer_us=13000000 request=3",
                "STARRY_T2N1_FAULT_RECOVERY_COMPLETE mode=normal "
                "safe_observed=true recovered=true",
                "TASK2_CONTROL_RECEIVED seq=1 request=3",
            )
        )

        self.assertEqual(VERIFY.verify_blackout(frames, complete_log), [])

    def test_task2_blackout_accepts_task2_recovery_mode(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_STATUS,
                sequence=1,
            ),
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_CONTROL,
                sequence=1,
            ),
            frame(
                src=VERIFY.ZEPHYR_IP,
                dst=VERIFY.STARRY_IP,
                kind=VERIFY.KIND_STATUS,
                sequence=1,
            ),
        ]
        task2_log = "\n".join(
            (
                "TASK2_CONTROL_RECEIVED seq=1 request=1",
                "virtnet: blackout ON",
                "STARRY_T2N1_SAFE source=protocol reason=RetryExhausted",
                "TASK2_SAFE state=Safe event=HeartbeatTimeout",
                "virtnet: blackout OFF",
                "STARRY_T2N1_RECOVERED state=Active",
                "STARRY_T2N1_FAULT_RECOVERY_COMPLETE mode=task2 "
                "safe_observed=true recovered=true",
                "TASK2_CONTROL_RECEIVED seq=1 request=3",
            )
        )

        self.assertEqual(VERIFY.verify_task2_only_blackout(frames, task2_log), [])

    def test_yolo_model_rejection_keeps_heartbeat_but_emits_no_control(self) -> None:
        frames = [
            frame(
                src=VERIFY.STARRY_IP,
                dst=VERIFY.ZEPHYR_IP,
                kind=VERIFY.KIND_HEARTBEAT,
            )
        ]
        log = "\n".join(
            (
                "TASK3_MODEL_READY model=yolo11n.ncnn runtime=ncnn "
                "mode=in-guest run_mode=model-rejected",
                "TASK3_MODEL_REJECTED model=yolo11n.ncnn "
                "reason=InjectedInvalidOutput action=safe",
                "STARRY_T2N1_SAFE source=model reason=InjectedInvalidOutput",
            )
        )

        self.assertEqual(VERIFY.verify_model_rejected(frames, log), [])

    def test_model_ready_allows_one_interleaved_kernel_log_line(self) -> None:
        log = (
            "TASK3_MODEL_READY model=yolo11n.ncnn runtime=ncnn bin_sha256=abc"
            "[ 1.0] eth0: ARP request\n"
            "def mode=in-guest run_mode=model-rejected samples=0"
        )

        self.assertIsNotNone(re.search(VERIFY.YOLO_READY_PATTERN, log))
        self.assertEqual(
            VERIFY.require_patterns(
                log,
                (
                    r"TASK3_MODEL_READY(?:[^\n]*\n){0,16}[^\n]*"
                    r"run_mode=model-rejected",
                ),
            ),
            [],
        )

    def test_model_ready_survives_a_guest_console_handoff(self) -> None:
        interleaved_zephyr_lines = "\n".join(
            f"TASK2_HEARTBEAT_RECEIVED peer_uptime_ms={index}"
            for index in range(14)
        )
        log = (
            "TASK3_MODEL_READY model=yolo11n.ncnn runtime=ncnn ncnn_revision=\n"
            "\n[Axvisor] attached VM[2] console; use Ctrl+X, then h to return "
            "to the shell\n"
            f"{interleaved_zephyr_lines}\n"
            "\n[Axvisor] attached VM[1] console; use Ctrl+X, then h to return "
            "to the shell\n"
            "946fe3fb14a8dff8c06df763f67be522167b2f00 "
            "param_sha256=d2c0adf8939dc9ce02964ce8ada104447768ffd8e3bffad8fa11e2e61e709c1f "
            "mode=in-guest run_mode=normal samples=0\n"
            "TASK3_INFER_STARTED model=yolo11n.ncnn request=1 phase=startup"
        )

        ready_record = VERIFY.task3_model_ready_record(log)

        self.assertNotIn("TASK2_HEARTBEAT_RECEIVED", ready_record)
        self.assertIsNotNone(re.search(VERIFY.YOLO_READY_PATTERN, ready_record))


if __name__ == "__main__":
    unittest.main()
