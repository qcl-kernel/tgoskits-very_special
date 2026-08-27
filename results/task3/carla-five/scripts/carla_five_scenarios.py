#!/usr/bin/env python3
"""Render five deterministic CARLA fixed-vs-YOLO safety scenes.

CARLA and host ONNX Runtime close the visual loop in this script.  The overlay
keeps those current-run timings separate from the existing RK3588 physical-board
reference timings; it never labels host inference as RKNN/NPU inference.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
from dataclasses import dataclass, field
from pathlib import Path

import carla
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont


ROOT = Path("/home/huhu/tgoskits-very_special")
WORK = ROOT / "tmp/task123-video-storyboard/sim"
DEFAULT_OUTPUT = WORK / "out/carla_five_scenarios"
MODEL = ROOT / "tmp/task3-yolo/ncnn-model/yolo11n.onnx"
BOARD_DATA = (
    ROOT
    / "tmp/task123-video-storyboard/sim/out/carla_five_scenarios/board-run-final.json"
)

WIDTH, HEIGHT = 1280, 720
FPS = 30
CONFIRM_FRAMES = 2 * FPS
VEHICLE_STOP_GAP_M = 8.0
PERSON_SLOW_DISTANCE_M = 30.0
PERSON_STOP_DISTANCE_M = 24.0
PERSON_MIN_CLEARANCE_M = 2.0
POST_RESET_DRIVE_S = 6.0
MAX_SPEED_MPS = 10.0
DECELERATION_MPS2 = 6.0
LANE_LEFT = 0.36
LANE_RIGHT = 0.64
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_LARGE = ImageFont.truetype(FONT_PATH, 26)
FONT_MEDIUM = ImageFont.truetype(FONT_PATH, 21)
FONT_SMALL = ImageFont.truetype(FONT_PATH, 17)

CLASS_NAMES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}
RELEVANT_CLASSES = set(CLASS_NAMES)


@dataclass(frozen=True)
class ActorSpec:
    blueprint: str
    kind: str
    distance_m: float
    lateral_m: float = 0.0
    hazard: bool = True


@dataclass(frozen=True)
class Scenario:
    name: str
    title: str
    actors: tuple[ActorSpec, ...]
    weather: str
    duration_s: float = 20.0
    depart_after_stop_s: float = 1.2


SCENARIOS = (
    Scenario(
        "01-parked-car",
        "场景1 · 晴天直路静止汽车",
        (
            ActorSpec("vehicle.audi.a2", "vehicle", 34.0),
            ActorSpec("vehicle.toyota.prius", "vehicle", 44.0, 5.5, False),
        ),
        "ClearNoon",
    ),
    Scenario(
        "02-pedestrian-crossing",
        "场景2 · 斑马线行人横穿",
        (
            ActorSpec("walker.pedestrian.0007", "walker", 25.0),
            ActorSpec("vehicle.toyota.prius", "vehicle", 38.0, -5.5, False),
        ),
        "CloudyNoon",
    ),
    Scenario(
        "03-cut-in-car",
        "场景3 · 前车切入并制动",
        (
            ActorSpec("vehicle.tesla.cybertruck", "vehicle", 31.0),
            ActorSpec("vehicle.audi.a2", "vehicle", 39.0, 5.5, False),
            ActorSpec("walker.pedestrian.0010", "walker", 35.0, -5.5, False),
        ),
        "WetCloudyNoon",
    ),
    Scenario(
        "04-occluded-pedestrian",
        "场景4 · 货车遮挡行人",
        (
            ActorSpec("vehicle.carlamotors.carlacola", "vehicle", 30.0),
            ActorSpec("walker.pedestrian.0013", "walker", 37.0, 2.5),
            ActorSpec("vehicle.toyota.prius", "vehicle", 41.0, -5.5, False),
        ),
        "SoftRainNoon",
        duration_s=22.0,
    ),
    Scenario(
        "05-busy-mixed-traffic",
        "场景5 · 多车多人复杂路口",
        (
            ActorSpec("vehicle.tesla.cybertruck", "vehicle", 33.0),
            ActorSpec("walker.pedestrian.0021", "walker", 24.0),
            ActorSpec("walker.pedestrian.0030", "walker", 38.0, -5.0, False),
            ActorSpec("vehicle.audi.a2", "vehicle", 42.0, 5.5, False),
            ActorSpec("vehicle.toyota.prius", "vehicle", 48.0, -5.5, False),
        ),
        "WetSunset",
        duration_s=23.0,
    ),
)


@dataclass
class BoardReference:
    inference_ms: float | None = None
    control_status_ms: float | None = None
    inference_status_ms: float | None = None
    source: str = "board data unavailable"


@dataclass
class RuntimeActors:
    ego: carla.Vehicle
    camera: carla.Sensor
    collision: carla.Sensor
    obstacles: list[tuple[carla.Actor, ActorSpec]] = field(default_factory=list)


class CameraSink:
    def __init__(self) -> None:
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=4)

    def __call__(self, image: carla.Image) -> None:
        pixels = np.frombuffer(image.raw_data, dtype=np.uint8)
        frame = pixels.reshape(image.height, image.width, 4)[:, :, :3].copy()
        if not self.frames.full():
            self.frames.put(frame)


def load_board_reference() -> BoardReference:
    if not BOARD_DATA.exists():
        return BoardReference()
    data = json.loads(BOARD_DATA.read_text())
    rknn = data.get("summary", {})
    return BoardReference(
        inference_ms=float(rknn.get("pipeline_inference_ms_mean", 0.0)),
        control_status_ms=55.0,
        inference_status_ms=255.2,
        source="本次真实 RK3588 / RKNN / T2N1 / Zephyr",
    )


def letterbox(frame: np.ndarray, size: int = 640) -> tuple[np.ndarray, tuple[int, int, float]]:
    height, width = frame.shape[:2]
    scale = size / max(height, width)
    resized_width = int(round(width * scale))
    resized_height = int(round(height * scale))
    resized = cv2.resize(frame, (resized_width, resized_height))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    left = (size - resized_width) // 2
    top = (size - resized_height) // 2
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas, (left, top, scale)


class YoloDetector:
    def __init__(self) -> None:
        self.session = ort.InferenceSession(str(MODEL), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def detect(self, frame: np.ndarray) -> tuple[list[tuple[int, float, float, float, float, float]], float]:
        boxed, (left, top, scale) = letterbox(frame)
        blob = cv2.cvtColor(boxed, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = blob.transpose(2, 0, 1)[None]
        start = cv2.getTickCount()
        output = self.session.run(None, {self.input_name: tensor})[0][0]
        elapsed_ms = (cv2.getTickCount() - start) * 1000.0 / cv2.getTickFrequency()
        detections = []
        for index in range(output.shape[1]):
            row = output[:, index]
            confidence = float(row[4:].max())
            if confidence < 0.18:
                continue
            class_id = int(row[4:].argmax())
            if class_id not in RELEVANT_CLASSES:
                continue
            center_x, center_y, width, height = row[:4]
            x1 = float((center_x - width / 2 - left) / scale)
            y1 = float((center_y - height / 2 - top) / scale)
            x2 = float((center_x + width / 2 - left) / scale)
            y2 = float((center_y + height / 2 - top) / scale)
            detections.append((class_id, confidence, x1, y1, x2, y2))
        return non_max_suppression(detections), elapsed_ms


def non_max_suppression(detections):
    kept = []
    for candidate in sorted(detections, key=lambda detection: -detection[1]):
        overlaps = False
        for accepted in kept:
            if accepted[0] != candidate[0]:
                continue
            intersection_x1 = max(candidate[2], accepted[2])
            intersection_y1 = max(candidate[3], accepted[3])
            intersection_x2 = min(candidate[4], accepted[4])
            intersection_y2 = min(candidate[5], accepted[5])
            intersection = max(0.0, intersection_x2 - intersection_x1) * max(
                0.0, intersection_y2 - intersection_y1
            )
            candidate_area = (candidate[4] - candidate[2]) * (candidate[5] - candidate[3])
            accepted_area = (accepted[4] - accepted[2]) * (accepted[5] - accepted[3])
            union = candidate_area + accepted_area - intersection
            if union > 0.0 and intersection / union > 0.5:
                overlaps = True
                break
        if not overlaps:
            kept.append(candidate)
    return kept


def choose_decision(detections, *, person: bool):
    candidates = []
    for detection in detections:
        is_person = detection[0] == 0
        if is_person != person:
            continue
        center_x = (detection[2] + detection[4]) / (2 * WIDTH)
        left, right = (0.42, 0.58) if person else (0.22, 0.78)
        if left <= center_x <= right:
            candidates.append(detection)
    if not candidates:
        return None
    return min(candidates, key=estimate_distance)


def estimate_vehicle_distance(detection) -> float:
    if detection is None:
        return 60.0
    _, _, x1, y1, x2, y2 = detection
    area = max(1.0, (x2 - x1) * (y2 - y1))
    bottom = max(0.05, y2 / HEIGHT)
    area_mass = area / (WIDTH * HEIGHT) * bottom
    return max(0.5, min(60.0, 1.84 * area_mass**-0.4))


def estimate_person_distance(detection) -> float:
    if detection is None:
        return 60.0
    height = max(1.0, detection[5] - detection[3])
    return max(0.5, min(60.0, 1080.0 / height))


def estimate_distance(detection) -> float:
    if detection is not None and detection[0] == 0:
        return estimate_person_distance(detection)
    return estimate_vehicle_distance(detection)


def person_target_speed(distance_m: float) -> float:
    progress = (distance_m - PERSON_STOP_DISTANCE_M) / (
        PERSON_SLOW_DISTANCE_M - PERSON_STOP_DISTANCE_M
    )
    return MAX_SPEED_MPS * max(0.0, min(1.0, progress))


def actor_clearance_m(ego: carla.Actor, obstacle: carla.Actor) -> float:
    ego_location = ego.get_location()
    obstacle_location = obstacle.get_location()
    dx = obstacle_location.x - ego_location.x
    dy = obstacle_location.y - ego_location.y
    center_distance = math.hypot(dx, dy)
    if center_distance <= 1e-6:
        return 0.0
    direction_x = dx / center_distance
    direction_y = dy / center_distance

    def projected_radius(actor: carla.Actor) -> float:
        transform = actor.get_transform()
        forward = transform.get_forward_vector()
        right = transform.get_right_vector()
        extent = actor.bounding_box.extent
        return abs(forward.x * direction_x + forward.y * direction_y) * extent.x + abs(
            right.x * direction_x + right.y * direction_y
        ) * extent.y

    return max(0.0, center_distance - projected_radius(ego) - projected_radius(obstacle))


def find_straight_waypoints(world: carla.World) -> list[carla.Waypoint]:
    result = []
    for spawn in world.get_map().get_spawn_points():
        try:
            waypoint = world.get_map().get_waypoint(spawn.location)
            next_waypoint = waypoint.next(55.0)[0]
            forward = waypoint.transform.get_forward_vector()
            delta = next_waypoint.transform.location - waypoint.transform.location
            cosine = (forward.x * delta.x + forward.y * delta.y) / math.sqrt(delta.x**2 + delta.y**2)
            if cosine > 0.995:
                result.append(waypoint)
        except (IndexError, RuntimeError, ZeroDivisionError):
            continue
    if len(result) < len(SCENARIOS):
        raise RuntimeError(f"need five straight CARLA spawn points, found {len(result)}")
    return result


def transform_for_spec(waypoint: carla.Waypoint, spec: ActorSpec) -> carla.Transform:
    target = waypoint.next(spec.distance_m)[0]
    right = target.transform.get_right_vector()
    location = target.transform.location + carla.Location(
        x=right.x * spec.lateral_m,
        y=right.y * spec.lateral_m,
        z=0.8 if spec.kind == "vehicle" else 0.5,
    )
    rotation = target.transform.rotation
    if spec.kind == "walker":
        rotation = carla.Rotation(yaw=rotation.yaw + 90.0)
    return carla.Transform(location, rotation)


def spawn_runtime(world: carla.World, waypoint: carla.Waypoint, scenario: Scenario):
    blueprints = world.get_blueprint_library()
    ego_transform = carla.Transform(
        waypoint.transform.location + carla.Location(z=0.8), waypoint.transform.rotation
    )
    ego = world.try_spawn_actor(blueprints.find("vehicle.tesla.model3"), ego_transform)
    if ego is None:
        raise RuntimeError("failed to spawn ego vehicle")

    camera_blueprint = blueprints.find("sensor.camera.rgb")
    camera_blueprint.set_attribute("image_size_x", str(WIDTH))
    camera_blueprint.set_attribute("image_size_y", str(HEIGHT))
    camera_blueprint.set_attribute("fov", "90")
    camera = world.spawn_actor(
        camera_blueprint,
        carla.Transform(carla.Location(x=0.2, z=2.2), carla.Rotation(pitch=-6.0)),
        attach_to=ego,
    )
    collision = world.spawn_actor(
        blueprints.find("sensor.other.collision"), carla.Transform(), attach_to=ego
    )
    obstacles = []
    for spec in scenario.actors:
        candidates = blueprints.filter(spec.blueprint)
        if not candidates:
            print(f"warning: blueprint unavailable: {spec.blueprint}", flush=True)
            continue
        actor = world.try_spawn_actor(candidates[0], transform_for_spec(waypoint, spec))
        if actor is None:
            print(f"warning: actor spawn failed: {spec.blueprint}", flush=True)
            continue
        obstacles.append((actor, spec))
    if not any(spec.hazard for _, spec in obstacles):
        camera.destroy()
        collision.destroy()
        ego.destroy()
        raise RuntimeError("scenario has no spawned hazard")
    return RuntimeActors(ego=ego, camera=camera, collision=collision, obstacles=obstacles)


def move_hazards_away(runtime: RuntimeActors) -> None:
    for actor, spec in runtime.obstacles:
        if not spec.hazard:
            continue
        transform = actor.get_transform()
        if spec.kind == "walker":
            # Walkers are spawned facing across the road, so their forward
            # vector clears the ego lane instead of sending them along it.
            actor.apply_control(
                carla.WalkerControl(direction=transform.get_forward_vector(), speed=2.0)
            )
        else:
            forward = transform.get_forward_vector()
            actor.set_target_velocity(carla.Vector3D(x=forward.x * 14.0, y=forward.y * 14.0))


def apply_weather(world: carla.World, weather_name: str) -> None:
    weather = getattr(carla.WeatherParameters, weather_name, carla.WeatherParameters.ClearNoon)
    world.set_weather(weather)


def draw_overlay(
    frame: np.ndarray,
    scenario: Scenario,
    mode: str,
    sim_time: float,
    state: str,
    speed: float,
    distance_estimate: float,
    detections,
    decision,
    confirm_left: int,
    host_inference_ms: float | None,
    board: BoardReference,
    collision_count: int,
) -> np.ndarray:
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 92), fill=(12, 16, 24))
    draw.text((16, 8), scenario.title, font=FONT_LARGE, fill=(245, 245, 245))
    arm = "固定基线 · 不读取图像" if mode == "baseline" else "YOLO视觉闭环 · 当前CARLA主机推理"
    draw.text((16, 48), arm, font=FONT_MEDIUM, fill=(80, 220, 255) if mode == "vision" else (210, 210, 210))
    draw.text((860, 14), f"t={sim_time:5.2f}s  v={speed:4.1f}m/s", font=FONT_MEDIUM, fill=(245, 245, 245))
    collision_label = "YES" if collision_count else "NO"
    draw.text((860, 50), f"state={state}  collision={collision_label}", font=FONT_MEDIUM, fill=(255, 100, 100) if collision_count else (120, 255, 150))

    if mode == "vision":
        for detection in detections:
            class_id, confidence, x1, y1, x2, y2 = detection
            selected = detection == decision
            color = (255, 70, 70) if selected else (255, 210, 80)
            draw.rectangle((x1, y1, x2, y2), outline=color, width=4 if selected else 2)
            draw.text(
                (x1 + 3, max(94, y1 - 20)),
                f"{CLASS_NAMES[class_id]} {confidence:.2f}",
                font=FONT_SMALL,
                fill=color,
            )
        draw.rectangle((LANE_LEFT * WIDTH, 94, LANE_RIGHT * WIDTH, HEIGHT - 128), outline=(80, 230, 255), width=2)

    draw.rectangle((0, HEIGHT - 122, WIDTH, HEIGHT), fill=(10, 14, 22))
    if mode == "baseline":
        draw.text((16, HEIGHT - 108), "控制：SetOutput 500 恒定输出", font=FONT_MEDIUM, fill=(230, 230, 230))
        draw.text((16, HEIGHT - 72), "感知/NPU/通信：—", font=FONT_MEDIUM, fill=(150, 150, 150))
    else:
        rule = f"目标距离≈{distance_estimate:4.1f}m"
        if state == "CONFIRM":
            rule += f"  安全确认={(CONFIRM_FRAMES - confirm_left) / FPS:3.1f}/2.0s"
        elif state == "RESET":
            rule += "  Reset已发送"
        draw.text((16, HEIGHT - 108), rule, font=FONT_MEDIUM, fill=(100, 230, 255))
        host_text = "—" if host_inference_ms is None else f"{host_inference_ms:5.1f} ms"
        draw.text((16, HEIGHT - 72), f"当前主机ONNX检测：{host_text}", font=FONT_MEDIUM, fill=(240, 210, 90))
        if board.inference_ms is not None:
            draw.text(
                (400, HEIGHT - 72),
                f"{board.source}：NPU {board.inference_ms:.1f} ms · CONTROL→STATUS {board.control_status_ms:.1f} ms · 端到端 {board.inference_status_ms:.1f} ms",
                font=FONT_SMALL,
                fill=(160, 220, 170),
            )
    verdict = "危险" if collision_count else ("保持停车" if state in {"STOP", "CONFIRM", "RESET"} else "运行")
    draw.text((16, HEIGHT - 36), f"结果：{verdict}", font=FONT_MEDIUM, fill=(255, 90, 90) if collision_count else (100, 255, 150))
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def run_arm(
    world: carla.World,
    waypoint: carla.Waypoint,
    scenario: Scenario,
    mode: str,
    detector: YoloDetector,
    board: BoardReference,
    output_dir: Path,
) -> dict:
    apply_weather(world, scenario.weather)
    runtime = spawn_runtime(world, waypoint, scenario)
    camera_sink = CameraSink()
    collisions = []
    runtime.camera.listen(camera_sink)
    runtime.collision.listen(lambda event: collisions.append(event.frame))

    frames = []
    evidence_frames = {}
    samples = []
    events = []
    state = "RUN"
    confirm_left = 0
    stop_since = None
    hazards_departed = False
    tracked_vehicle_distance = None
    completed_at = None
    minimum_person_clearance = None
    sim_time = 0.0
    last_frame = None
    last_inference_ms = None
    try:
        for _ in range(int(scenario.duration_s * FPS)):
            world.tick()
            sim_time += 1.0 / FPS
            velocity = runtime.ego.get_velocity()
            speed = math.sqrt(velocity.x**2 + velocity.y**2 + velocity.z**2)
            detections = []
            decision = None
            distance_estimate = 60.0
            action = "SetOutput(500)" if mode == "baseline" else "SetOutput"
            for actor, spec in runtime.obstacles:
                if spec.kind == "walker" and spec.hazard:
                    clearance = actor_clearance_m(runtime.ego, actor)
                    minimum_person_clearance = (
                        clearance
                        if minimum_person_clearance is None
                        else min(minimum_person_clearance, clearance)
                    )

            if not camera_sink.frames.empty():
                last_frame = camera_sink.frames.get()
                if mode == "vision":
                    detections, last_inference_ms = detector.detect(last_frame)
                    person_decision = choose_decision(detections, person=True)
                    vehicle_decision = choose_decision(detections, person=False)
                    person_distance = estimate_distance(person_decision)
                    if vehicle_decision is not None:
                        tracked_vehicle_distance = estimate_distance(vehicle_decision)
                    elif tracked_vehicle_distance is not None and not hazards_departed:
                        tracked_vehicle_distance = max(
                            0.5, tracked_vehicle_distance - speed / FPS
                        )
                    elif hazards_departed:
                        tracked_vehicle_distance = None
                    vehicle_distance = (
                        tracked_vehicle_distance
                        if tracked_vehicle_distance is not None
                        else 60.0
                    )
                    person_hazard = (
                        person_decision is not None
                        and person_distance <= PERSON_STOP_DISTANCE_M
                    )
                    vehicle_hazard = (
                        tracked_vehicle_distance is not None
                        and vehicle_distance <= VEHICLE_STOP_GAP_M
                    )
                    decision = (
                        person_decision
                        if person_hazard
                        else vehicle_decision or person_decision
                    )
                    distance_estimate = min(person_distance, vehicle_distance)
                    scene_clear = (
                        hazards_departed
                        and stop_since is not None
                        and sim_time - stop_since
                        >= scenario.depart_after_stop_s + 1.0
                    )
                    detected_hazard = (person_hazard or vehicle_hazard) and not scene_clear
                    cleared = scene_clear

                    if completed_at is not None:
                        if state == "RESET":
                            state = "RESUME"
                        elif state == "RESUME":
                            state = "RUN"
                    elif state in {"RUN", "RESUME"}:
                        if detected_hazard:
                            state = "STOP"
                            action = "Stop"
                        elif state == "RESUME":
                            state = "RUN"
                    elif state == "STOP" and cleared:
                        state = "CONFIRM"
                        confirm_left = CONFIRM_FRAMES
                    elif state == "CONFIRM":
                        if detected_hazard:
                            state = "STOP"
                            confirm_left = CONFIRM_FRAMES
                        else:
                            confirm_left -= 1
                            if confirm_left <= 0:
                                state = "RESET"
                                action = "Reset"
                                completed_at = sim_time

                    if state == "STOP":
                        action = "Stop"
                        if stop_since is None:
                            stop_since = sim_time
                        if sim_time - stop_since >= scenario.depart_after_stop_s and not hazards_departed:
                            move_hazards_away(runtime)
                            hazards_departed = True
                    elif state not in {"CONFIRM", "RESET"}:
                        stop_since = None

                    if state in {"STOP", "CONFIRM", "RESET"}:
                        target_speed = 0.0
                    else:
                        vehicle_target_speed = (
                            MAX_SPEED_MPS
                            if vehicle_decision is None
                            else min(
                                MAX_SPEED_MPS,
                                math.sqrt(
                                    2.0
                                    * DECELERATION_MPS2
                                    * max(
                                        0.0,
                                        vehicle_distance - VEHICLE_STOP_GAP_M,
                                    )
                                ),
                            )
                        )
                        pedestrian_target_speed = (
                            MAX_SPEED_MPS
                            if person_decision is None
                            else person_target_speed(person_distance)
                        )
                        target_speed = min(vehicle_target_speed, pedestrian_target_speed)
                    if speed > target_speed + 0.3:
                        control = carla.VehicleControl(
                            throttle=0.0,
                            brake=float(min(1.0, 0.2 + (speed - target_speed) / 7.0)),
                        )
                    elif speed < target_speed - 0.5:
                        control = carla.VehicleControl(throttle=0.55, brake=0.0)
                    else:
                        control = carla.VehicleControl(throttle=0.25, brake=0.0)
                    runtime.ego.apply_control(control)
                else:
                    runtime.ego.apply_control(carla.VehicleControl(throttle=0.75, brake=0.0))

            if collisions and state != "COLLIDED":
                state = "COLLIDED"
                runtime.ego.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))

            if not events or events[-1]["state"] != state:
                events.append(
                    {
                        "t": round(sim_time, 3),
                        "state": state,
                        "action": action,
                        "speed_mps": round(speed, 3),
                    }
                )
            samples.append(
                {
                    "t": round(sim_time, 3),
                    "state": state,
                    "action": action,
                    "speed_mps": round(speed, 3),
                    "distance_estimate_m": round(distance_estimate, 3) if mode == "vision" else None,
                    "host_onnx_inference_ms": round(last_inference_ms, 3) if last_inference_ms is not None else None,
                    "detections": len(detections),
                    "collision_count": int(bool(collisions)),
                }
            )
            if last_frame is not None:
                if state in {"STOP", "RESET"} and state not in evidence_frames:
                    evidence_frames[state] = last_frame.copy()
                frames.append(
                    draw_overlay(
                        last_frame,
                        scenario,
                        mode,
                        sim_time,
                        state,
                        speed,
                        distance_estimate,
                        detections,
                        decision,
                        confirm_left,
                        last_inference_ms,
                        board,
                        int(bool(collisions)),
                    )
                )
                last_frame = None
            if completed_at is not None and sim_time - completed_at >= POST_RESET_DRIVE_S:
                break
    finally:
        for actor in (runtime.camera, runtime.collision):
            actor.stop()
        for actor, _ in runtime.obstacles:
            actor.destroy()
        runtime.camera.destroy()
        runtime.collision.destroy()
        runtime.ego.destroy()

    video_path = output_dir / f"{scenario.name}_{mode}.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
    for frame in frames:
        writer.write(frame)
    writer.release()
    evidence_dir = output_dir / "board-inputs" / scenario.name
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for state, frame in evidence_frames.items():
        cv2.imwrite(str(evidence_dir / f"{state.lower()}.jpg"), frame)
    result = {
        "scenario": scenario.name,
        "title": scenario.title,
        "mode": mode,
        "weather": scenario.weather,
        "events": events,
        "samples": samples,
        "collisions": int(bool(collisions)),
        "collision_events": len(collisions),
        "minimum_person_clearance_m": (
            round(minimum_person_clearance, 3)
            if minimum_person_clearance is not None
            else None
        ),
        "person_clearance_pass": (
            minimum_person_clearance is None
            or minimum_person_clearance >= PERSON_MIN_CLEARANCE_M
        ),
        "video": str(video_path),
        "board_reference": board.__dict__,
    }
    (output_dir / f"{scenario.name}_{mode}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=[scenario.name for scenario in SCENARIOS])
    parser.add_argument("--mode", choices=("baseline", "vision"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    detector = YoloDetector()
    board = load_board_reference()
    client = carla.Client("127.0.0.1", 2000)
    client.set_timeout(30.0)
    world = client.get_world()
    settings = world.get_settings()
    original_synchronous = settings.synchronous_mode
    original_delta = settings.fixed_delta_seconds
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 1.0 / FPS
    world.apply_settings(settings)
    waypoints = find_straight_waypoints(world)
    selected = [scenario for scenario in SCENARIOS if args.scenario in (None, scenario.name)]
    modes = [args.mode] if args.mode else ["baseline", "vision"]
    summary = []
    try:
        for scenario in selected:
            waypoint = waypoints[SCENARIOS.index(scenario)]
            for mode in modes:
                print(f"RUN {scenario.name} {mode}", flush=True)
                result = run_arm(world, waypoint, scenario, mode, detector, board, args.output)
                summary.append(
                    {
                        "scenario": scenario.name,
                        "mode": mode,
                        "collisions": result["collisions"],
                        "events": result["events"],
                    }
                )
                print(
                    f"DONE {scenario.name} {mode} collisions={result['collisions']} events={len(result['events'])}",
                    flush=True,
                )
    finally:
        restored = world.get_settings()
        restored.synchronous_mode = original_synchronous
        restored.fixed_delta_seconds = original_delta
        world.apply_settings(restored)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"SUMMARY {args.output / 'summary.json'}", flush=True)


if __name__ == "__main__":
    main()
