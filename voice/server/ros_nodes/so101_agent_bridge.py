"""HTTP/voice-plan to the existing SO-101 ROS topic protocol.

The node owns perception sampling and is the only component allowed to expand a
high-level TaskPlan into low-level robot steps.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from typing import Any

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String
from vision_msgs.msg import Detection2DArray

from ros_nodes.http_utils import JsonHandler
from so101_protocol import (
    COMMAND_TOPIC,
    DETECTION_TOPIC,
    STATUS_TOPIC,
    CoordinateFrame,
    ExecutionRequest,
    PoseMM,
    RobotStatus,
    RobotStep,
    RobotTask,
    build_pick_steps,
    build_place_steps,
    canonical_detection_name,
)


@dataclass(frozen=True)
class Detection:
    name: str
    score: float
    pose: PoseMM
    received_at: float


@dataclass
class PendingExecution:
    request: ExecutionRequest
    created_at: float
    object_samples: list[PoseMM] = field(default_factory=list)
    destination_samples: list[PoseMM] = field(default_factory=list)


def parse_color(detection_id: str) -> str:
    match = re.search(r"(?:^|:)color=([^:]+)", detection_id, flags=re.IGNORECASE)
    return match.group(1).strip().casefold() if match else ""


def detection_from_message(detection: Any, now: float) -> Detection | None:
    if not detection.results:
        return None
    result = max(detection.results, key=lambda item: float(item.hypothesis.score))
    class_name = str(result.hypothesis.class_id).strip().casefold()
    color = parse_color(str(detection.id))
    position = result.pose.pose.position
    pose = PoseMM(
        x=float(position.x) * 1000.0,
        y=float(position.y) * 1000.0,
        z=float(position.z) * 1000.0,
    )
    return Detection(
        name=canonical_detection_name(class_name, color),
        score=float(result.hypothesis.score),
        pose=pose,
        received_at=now,
    )


def stable_median(samples: list[PoseMM], max_spread_mm: float) -> PoseMM | None:
    if not samples:
        return None
    axes = ([pose.x for pose in samples], [pose.y for pose in samples], [pose.z for pose in samples])
    if any(max(values) - min(values) > max_spread_mm for values in axes):
        return None
    return PoseMM(
        x=statistics.median(axes[0]),
        y=statistics.median(axes[1]),
        z=statistics.median(axes[2]),
    )


class SO101AgentBridge(Node):
    def __init__(self) -> None:
        super().__init__("so101_agent_bridge")
        self.samples_n = int(self.declare_parameter("samples_n", 5).value)
        self.min_confidence = float(self.declare_parameter("min_confidence", 0.5).value)
        self.max_spread_mm = float(self.declare_parameter("max_sample_spread_mm", 50.0).value)
        self.detection_max_age_s = float(self.declare_parameter("detection_max_age_s", 2.0).value)
        self.pending_timeout_s = float(self.declare_parameter("pending_timeout_s", 15.0).value)
        self.lift_mm = float(self.declare_parameter("lift_mm", 150.0).value)
        self.return_home = bool(self.declare_parameter("return_home", True).value)
        self.home = PoseMM(
            x=float(self.declare_parameter("home.x", -78.7).value),
            y=float(self.declare_parameter("home.y", 6.7).value),
            z=float(self.declare_parameter("home.z", 474.7).value),
        )
        if self.samples_n < 1:
            raise ValueError("samples_n must be at least 1")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if self.max_spread_mm < 0 or self.detection_max_age_s <= 0 or self.pending_timeout_s <= 0:
            raise ValueError("sampling distances and timeouts must be positive")

        self.command_pub = self.create_publisher(String, COMMAND_TOPIC, 10)
        self.detection_sub = self.create_subscription(
            Detection2DArray,
            DETECTION_TOPIC,
            self.detection_callback,
            qos_profile_sensor_data,
        )
        self.status_sub = self.create_subscription(String, STATUS_TOPIC, self.status_callback, 10)
        self.create_timer(0.5, self.timeout_timer)
        self.lock = threading.Lock()
        self.inventory: dict[str, Detection] = {}
        self.detection_message_count = 0
        self.parsed_detection_count = 0
        self.last_detection_at: float | None = None
        self.last_frame_objects: list[str] = []
        self.pending: PendingExecution | None = None
        self.active_task_id: str | None = None
        self.statuses: dict[str, dict[str, Any]] = {}
        self.get_logger().info(
            f"SO-101 bridge ready: {DETECTION_TOPIC} -> {COMMAND_TOPIC}; samples={self.samples_n}"
        )

    def object_names(self) -> list[str]:
        cutoff = time.monotonic() - self.detection_max_age_s
        with self.lock:
            return sorted(
                name
                for name, detection in self.inventory.items()
                if detection.received_at >= cutoff and detection.score >= self.min_confidence
            )

    def perception_health(self) -> dict[str, Any]:
        now = time.monotonic()
        with self.lock:
            last_age = (
                round(now - self.last_detection_at, 3)
                if self.last_detection_at is not None
                else None
            )
            inventory = {
                name: {
                    "score": round(detection.score, 3),
                    "age_seconds": round(now - detection.received_at, 3),
                }
                for name, detection in sorted(self.inventory.items())
            }
            return {
                "messages_received": self.detection_message_count,
                "detections_parsed": self.parsed_detection_count,
                "last_message_age_seconds": last_age,
                "last_frame_objects": list(self.last_frame_objects),
                "inventory": inventory,
                "visible_objects": sorted(
                    name
                    for name, detection in self.inventory.items()
                    if now - detection.received_at <= self.detection_max_age_s
                    and detection.score >= self.min_confidence
                ),
                "detection_max_age_seconds": self.detection_max_age_s,
                "ros_publishers": self.count_publishers(DETECTION_TOPIC),
            }

    def submit(self, request: ExecutionRequest) -> dict[str, Any]:
        if request.plan.requires_confirmation:
            raise ValueError("plan still requires confirmation")
        behavior = request.plan.behavior
        if behavior == "stop":
            return self.stop(request.request_id)
        if behavior == "home":
            self.reserve_direct_task(request.request_id)
            try:
                task = RobotTask(
                    task_id=self.safe_task_id(request.request_id),
                    steps=[self.home_step()],
                )
                self.publish_task(task)
            except Exception:
                self.release_task(self.safe_task_id(request.request_id))
                raise
            return self.accepted(request.request_id, "QUEUED", "home task published")
        if behavior == "move_to":
            coordinates = request.plan.coordinates
            if coordinates is None or coordinates.frame_id != "base_link":
                raise ValueError("SO-101 move_to requires coordinates in base_link")
            self.reserve_direct_task(request.request_id)
            try:
                task = RobotTask(
                    task_id=self.safe_task_id(request.request_id),
                    steps=[
                        RobotStep(
                            action="move",
                            frame=CoordinateFrame.ROBOT,
                            x=coordinates.x * 1000.0,
                            y=coordinates.y * 1000.0,
                            z=coordinates.z * 1000.0,
                        )
                    ],
                )
                self.publish_task(task)
            except Exception:
                self.release_task(self.safe_task_id(request.request_id))
                raise
            return self.accepted(request.request_id, "QUEUED", "move_to task published")

        if behavior not in {"pick", "place", "pick_and_place"}:
            raise ValueError(f"unsupported behavior: {behavior}")
        with self.lock:
            if self.pending is not None or self.active_task_id is not None:
                current = (
                    self.pending.request.request_id if self.pending else self.active_task_id
                )
                raise RuntimeError(f"request {current} is already active")
            self.pending = PendingExecution(request=request, created_at=time.monotonic())
            self.statuses[request.request_id] = {
                "request_id": request.request_id,
                "state": "SAMPLING",
                "message": "waiting for stable perception coordinates",
            }
        return self.accepted(request.request_id, "SAMPLING", "waiting for stable perception")

    def preview(self, request: ExecutionRequest) -> dict[str, Any]:
        """Build robot steps from the latest CV snapshot without publishing them."""
        if request.plan.requires_confirmation:
            raise ValueError("plan still requires confirmation")

        behavior = request.plan.behavior
        object_pose: PoseMM | None = None
        destination_pose: PoseMM | None = None
        perception: dict[str, dict[str, Any]] = {}

        if behavior in {"pick", "place", "pick_and_place"}:
            names: list[str] = []
            if behavior in {"pick", "pick_and_place"} and request.plan.object:
                names.append(request.plan.object)
            if behavior in {"place", "pick_and_place"} and request.plan.destination:
                names.append(request.plan.destination)

            now = time.monotonic()
            with self.lock:
                selected: dict[str, Detection] = {}
                for name in names:
                    detection = self.inventory.get(name)
                    if detection is None or now - detection.received_at > self.detection_max_age_s:
                        raise ValueError(f"fresh CV coordinates are unavailable for {name}")
                    if detection.score < self.min_confidence:
                        raise ValueError(f"CV confidence is too low for {name}")
                    selected[name] = detection

            if request.plan.object and request.plan.object in selected:
                object_pose = selected[request.plan.object].pose
            if request.plan.destination and request.plan.destination in selected:
                destination_pose = selected[request.plan.destination].pose
            perception = {
                name: {
                    "score": round(detection.score, 3),
                    "frame": "camera",
                    "x_mm": round(detection.pose.x, 1),
                    "y_mm": round(detection.pose.y, 1),
                    "z_mm": round(detection.pose.z, 1),
                }
                for name, detection in selected.items()
            }

        if behavior == "stop":
            task = RobotTask(
                task_id=self.safe_task_id(request.request_id),
                steps=[RobotStep(action="stop")],
            )
        elif behavior == "home":
            task = RobotTask(
                task_id=self.safe_task_id(request.request_id),
                steps=[self.home_step()],
            )
        elif behavior == "move_to":
            coordinates = request.plan.coordinates
            if coordinates is None or coordinates.frame_id != "base_link":
                raise ValueError("SO-101 move_to requires coordinates in base_link")
            task = RobotTask(
                task_id=self.safe_task_id(request.request_id),
                steps=[
                    RobotStep(
                        action="move",
                        frame=CoordinateFrame.ROBOT,
                        x=coordinates.x * 1000.0,
                        y=coordinates.y * 1000.0,
                        z=coordinates.z * 1000.0,
                    )
                ],
            )
        elif behavior in {"pick", "place", "pick_and_place"}:
            task = self.build_task(request, object_pose, destination_pose)
        else:
            raise ValueError(f"unsupported behavior: {behavior}")

        return {
            "request_id": request.request_id,
            "preview_only": True,
            "coordinate_source": "latest_cv_snapshot",
            "perception": perception,
            "task": task.to_dict(),
        }

    def stop(self, request_id: str = "stop") -> dict[str, Any]:
        with self.lock:
            self.pending = None
            self.active_task_id = None
        msg = String()
        msg.data = json.dumps({"action": "stop", "task_id": self.safe_task_id(request_id)})
        self.command_pub.publish(msg)
        return self.accepted(request_id, "STOP_REQUESTED", "stop published immediately")

    def detection_callback(self, msg: Detection2DArray) -> None:
        now = time.monotonic()
        frame: dict[str, Detection] = {}
        for raw in msg.detections:
            try:
                detection = detection_from_message(raw, now)
            except (ValueError, TypeError, AttributeError) as error:
                self.get_logger().warning(f"invalid detection skipped: {error}")
                continue
            if detection and detection.score >= self.min_confidence:
                previous = frame.get(detection.name)
                if previous is None or detection.score > previous.score:
                    frame[detection.name] = detection

        with self.lock:
            self.detection_message_count += 1
            self.parsed_detection_count += len(frame)
            self.last_detection_at = now
            self.last_frame_objects = sorted(frame)
            previous_names = set(self.inventory)
            self.inventory.update(frame)
            current_names = set(self.inventory)
            pending = self.pending
            if current_names != previous_names:
                self.get_logger().info(
                    f"Perception inventory updated: {', '.join(sorted(current_names)) or '(empty)'}"
                )
            if pending is None:
                return
            plan = pending.request.plan
            needs_object = plan.behavior in {"pick", "pick_and_place"}
            needs_destination = plan.behavior in {"place", "pick_and_place"}
            object_detection = frame.get(plan.object or "") if needs_object else None
            destination_detection = frame.get(plan.destination or "") if needs_destination else None
            # Samples must be consecutive frames containing every required entity.
            if (needs_object and object_detection is None) or (
                needs_destination and destination_detection is None
            ):
                pending.object_samples.clear()
                pending.destination_samples.clear()
                return
            if object_detection:
                pending.object_samples.append(object_detection.pose)
                pending.object_samples = pending.object_samples[-self.samples_n :]
            if destination_detection:
                pending.destination_samples.append(destination_detection.pose)
                pending.destination_samples = pending.destination_samples[-self.samples_n :]
            enough = (
                (not needs_object or len(pending.object_samples) >= self.samples_n)
                and (not needs_destination or len(pending.destination_samples) >= self.samples_n)
            )
            if not enough:
                return
            object_pose = (
                stable_median(pending.object_samples, self.max_spread_mm) if needs_object else None
            )
            destination_pose = (
                stable_median(pending.destination_samples, self.max_spread_mm)
                if needs_destination
                else None
            )
            if (needs_object and object_pose is None) or (
                needs_destination and destination_pose is None
            ):
                return
            request = pending.request
            self.pending = None
            self.active_task_id = self.safe_task_id(request.request_id)

        try:
            task = self.build_task(request, object_pose, destination_pose)
            self.publish_task(task)
            with self.lock:
                self.statuses[request.request_id] = {
                    "request_id": request.request_id,
                    "state": "QUEUED",
                    "message": "stable coordinates resolved; robot task published",
                }
        except Exception as error:
            with self.lock:
                self.active_task_id = None
                self.statuses[request.request_id] = {
                    "request_id": request.request_id,
                    "state": "REJECTED",
                    "message": str(error),
                }
            self.get_logger().error(f"failed to build task: {error}")

    def build_task(
        self,
        request: ExecutionRequest,
        object_pose: PoseMM | None,
        destination_pose: PoseMM | None,
    ) -> RobotTask:
        steps: list[RobotStep] = []
        behavior = request.plan.behavior
        if behavior in {"pick", "pick_and_place"}:
            if object_pose is None:
                raise ValueError("object pose is unavailable")
            steps.extend(build_pick_steps(object_pose, self.lift_mm))
        if behavior in {"place", "pick_and_place"}:
            if destination_pose is None:
                raise ValueError("destination pose is unavailable")
            steps.extend(build_place_steps(destination_pose))
        if self.return_home:
            steps.append(self.home_step())
        return RobotTask(task_id=self.safe_task_id(request.request_id), steps=steps)

    def home_step(self) -> RobotStep:
        return RobotStep(
            action="move",
            frame=CoordinateFrame.ROBOT,
            x=self.home.x,
            y=self.home.y,
            z=self.home.z,
        )

    def publish_task(self, task: RobotTask) -> None:
        msg = String()
        msg.data = task.to_json()
        self.command_pub.publish(msg)
        self.get_logger().info(f"Published validated task {task.task_id}: {msg.data}")

    def status_callback(self, msg: String) -> None:
        try:
            status = RobotStatus.from_json(msg.data)
        except (ValueError, json.JSONDecodeError) as error:
            self.get_logger().warning(f"invalid executor status ignored: {error}")
            return
        if status.task_id:
            with self.lock:
                self.statuses[status.task_id] = status.to_dict()
                if status.state in {"done", "error", "cancelled", "rejected", "stopped"}:
                    if self.active_task_id == status.task_id:
                        self.active_task_id = None

    def timeout_timer(self) -> None:
        with self.lock:
            pending = self.pending
            if pending is None or time.monotonic() - pending.created_at <= self.pending_timeout_s:
                return
            request_id = pending.request.request_id
            self.pending = None
            self.statuses[request_id] = {
                "request_id": request_id,
                "state": "TIMEOUT",
                "message": "required objects were not stably detected",
            }
        self.get_logger().warning(f"Perception timeout for {request_id}")

    def status(self, request_id: str) -> dict[str, Any] | None:
        with self.lock:
            return self.statuses.get(request_id)

    def reserve_direct_task(self, request_id: str) -> None:
        task_id = self.safe_task_id(request_id)
        with self.lock:
            if self.pending is not None or self.active_task_id is not None:
                current = (
                    self.pending.request.request_id if self.pending else self.active_task_id
                )
                raise RuntimeError(f"request {current} is already active")
            self.active_task_id = task_id

    def release_task(self, task_id: str) -> None:
        with self.lock:
            if self.active_task_id == task_id:
                self.active_task_id = None

    @staticmethod
    def safe_task_id(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "_", value)[:100]
        return cleaned or f"task_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def accepted(request_id: str, state: str, message: str) -> dict[str, Any]:
        return {"request_id": request_id, "accepted": True, "state": state, "message": message}


class BridgeHttpHandler(JsonHandler):
    bridge: SO101AgentBridge

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "backend": "so101-ros-topics",
                    "perception": self.bridge.perception_health(),
                },
            )
            return
        if self.path == "/objects":
            self.send_json(200, {"objects": self.bridge.object_names()})
            return
        if self.path.startswith("/status/"):
            request_id = self.path.removeprefix("/status/")
            status = self.bridge.status(request_id)
            self.send_json(200 if status else 404, status or {"error": "unknown request"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/preview":
                response = self.bridge.preview(ExecutionRequest.from_dict(self.read_json()))
            elif self.path == "/execute":
                response = self.bridge.submit(ExecutionRequest.from_dict(self.read_json()))
            elif self.path == "/stop":
                response = self.bridge.stop()
            else:
                self.send_json(404, {"error": "not found"})
                return
            self.send_json(200, response)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            self.send_json(409, {"error": str(error)})
        except Exception as error:
            self.send_json(500, {"error": str(error)})


def main(args: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SO-101 perception and HTTP execution bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    known_args, ros_args = parser.parse_known_args(args)
    rclpy.init(args=ros_args)
    node = SO101AgentBridge()
    BridgeHttpHandler.bridge = node
    try:
        server = ThreadingHTTPServer(
            (known_args.host, known_args.port), BridgeHttpHandler
        )
    except Exception:
        node.destroy_node()
        rclpy.try_shutdown()
        raise

    http_thread = threading.Thread(target=server.serve_forever, daemon=True)
    http_thread.start()
    print(f"SO-101 bridge: http://{known_args.host}:{known_args.port}")
    print(
        "Routes: GET /health, GET /objects, GET /status/<id>, "
        "POST /preview, POST /execute, POST /stop"
    )
    try:
        # Keep the ROS executor in the main thread.  This is portable across
        # rclpy/RMW versions (including Jazzy) and prevents a healthy HTTP
        # endpoint from masking a dead ROS callback thread.
        rclpy.spin(node)
    except (ExternalShutdownException, KeyboardInterrupt):
        pass
    finally:
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2.0)
        node.destroy_node()
        try:
            rclpy.try_shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
