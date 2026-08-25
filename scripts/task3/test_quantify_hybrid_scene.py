import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("quantify-hybrid-scene.py")
SPEC = importlib.util.spec_from_file_location("quantify_hybrid_scene", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


RKNN_OUTCOMES = {
    1: "track",
    2: "track",
    3: "track",
    4: "track",
    5: "track",
    6: "track",
    7: "stop-hazard",
    8: "stop-latched",
    9: "stop-latched",
    10: "stop-latched",
    11: "reset",
    12: "track",
}


def make_log(source: str) -> bytes:
    scene_source = "rknn" if source == "yolov8.rknn" else source
    lines = [
        f"TASK3_HYBRID_SCENE_BEGIN source={scene_source} communication_cpu=1 ai_cpu=0"
    ]
    state = 300
    generation = 0
    for index, event_id in enumerate(MODULE.IDS, 1):
        action = MODULE.EXPECTED[index]
        if source == "fixed-perception":
            action = "Reset" if index == 11 else "SetOutput"
        if index != 11:
            generation += 1
        value = 0 if action in {"Stop", "Reset"} else 500
        outcome = "reset" if index == 11 else "fixed"
        if source == "yolov8.rknn":
            outcome = RKNN_OUTCOMES[index]
        request = index
        infer_start = index * 1_000_000
        infer_end = infer_start if source == "fixed-perception" else infer_start + 20_000
        lines.append(
            "TASK3_CONTROL_SENT "
            f"elapsed_ms={index * 10} event_index={index} event_id={event_id} "
            f"source={source} generation={generation} request={request} action={action} "
            f"value={value} outcome={outcome} infer_start_ns={infer_start} "
            f"infer_end_ns={infer_end} seq={index}"
        )
        if source == "yolov8.rknn" and index != 11:
            class_id = 0 if 7 <= index <= 9 else 2
            center_x = MODULE.TRUTH.get(index, 500)
            lines.append(
                "TASK3_DETECTION "
                f"event_index={index} event_id={event_id} class={class_id} "
                f"confidence_milli=900 center_x_milli={center_x} center_y_milli=600 "
                f"area_milli=100 request={request}"
            )
        if action == "SetOutput":
            state += 1
        protocol_state = "Stopped" if action == "Stop" else "Active"
        status_ns = infer_end + 100_000
        lines.append(
            "TASK3_STATUS_RECEIVED "
            f"elapsed_ms={index * 10 + 1} event_index={index} event_id={event_id} "
            f"request={request} value={state} state={state} protocol_state={protocol_state} "
            f"rtt_ms=1 status_ns={status_ns} "
            f"end_to_end_us={(status_ns - infer_start) // 1_000}"
        )
    lines.append(
        f"TASK3_EXPERIMENT_COMPLETE source={source} events=12 statuses=12 elapsed_ms=121"
    )
    if source == "yolov8.rknn":
        lines.append(
            "TASK3_HYBRID_SCENE_END source=rknn controller_complete=1 producer_rc=0"
        )
    else:
        lines.append(
            "TASK3_HYBRID_SCENE_END source=fixed-perception controller_rc=0 producer_rc=na"
        )
    return ("\n".join(lines) + "\n").encode()


def prefix_vm1(content: bytes) -> bytes:
    return b"".join(b"[VM 1] \x1b[m" + line + b"\r\n" for line in content.splitlines())


class QuantifyHybridSceneTest(unittest.TestCase):
    def parse(self, content: bytes, source: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{source}.log"
            path.write_bytes(content)
            return MODULE.parse(path, source)

    def assert_rejected(self, content: bytes, source: str) -> None:
        with self.assertRaises(ValueError):
            self.parse(content, source)

    def test_accepts_a_fully_correlated_chain(self) -> None:
        result = self.parse(make_log("yolov8.rknn"), "rknn")
        self.assertEqual(result["correct_decisions"], 12)
        self.assertEqual(result["statuses"], 12)

    def test_uses_only_the_latest_vm_prefixed_scene(self) -> None:
        stale = make_log("fixed-perception")
        current = prefix_vm1(make_log("yolov8.rknn"))

        result = self.parse(stale + current, "rknn")

        self.assertEqual(result["correct_decisions"], 12)
        self.assertEqual(result["statuses"], 12)

    def test_requires_an_exact_scene_end_marker(self) -> None:
        content = make_log("yolov8.rknn").replace(
            b"TASK3_HYBRID_SCENE_END", b"TASK3_HYBRID_SCENE_INTERRUPTED", 1
        )
        self.assert_rejected(content, "rknn")

    def test_rejects_control_source_mismatch(self) -> None:
        content = make_log("fixed-perception").replace(
            b"source=fixed-perception", b"source=rknn", 1
        )
        self.assert_rejected(content, "fixed")

    def test_rejects_status_for_another_request(self) -> None:
        content = make_log("yolov8.rknn").replace(
            b"request=1 value=301", b"request=101 value=301", 1
        )
        self.assert_rejected(content, "rknn")

    def test_rejects_status_state_inconsistent_with_action(self) -> None:
        content = make_log("yolov8.rknn").replace(
            b"protocol_state=Stopped", b"protocol_state=Active", 1
        )
        self.assert_rejected(content, "rknn")

    def test_rejects_detection_for_another_request(self) -> None:
        content = make_log("yolov8.rknn").replace(
            b"area_milli=100 request=1", b"area_milli=100 request=101", 1
        )
        self.assert_rejected(content, "rknn")

    def test_requires_the_exact_completion_marker(self) -> None:
        content = make_log("yolov8.rknn").replace(
            b"source=yolov8.rknn events=12 statuses=12",
            b"source=fixed-perception events=99 statuses=0",
            1,
        )
        self.assert_rejected(content, "rknn")


if __name__ == "__main__":
    unittest.main()
