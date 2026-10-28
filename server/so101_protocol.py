"""Dependency-free validated wire protocol for the SO-101 ROS runtime."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


DETECTION_TOPIC = "/detected_objects"
COMMAND_TOPIC = "/agent/robot_command"
STATUS_TOPIC = "/robot_executor/status"
ALLOWED_ACTIONS = {"move", "move_xy", "rel", "open", "close", "pose", "wait", "stop"}
ALLOWED_BEHAVIORS = {"pick", "place", "pick_and_place", "move_to", "home", "stop"}


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _strict_object(data: Any, allowed: set[str], required: set[str] = set()) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("value must be a JSON object")
    extra = set(data) - allowed
    missing = required - set(data)
    if extra:
        raise ValueError(f"unexpected fields: {', '.join(sorted(extra))}")
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    return data


class CoordinateFrame(str, Enum):
    CAMERA = "camera"
    ROBOT = "robot"


@dataclass(frozen=True)
class PoseMM:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite(self.x, "x"))
        object.__setattr__(self, "y", _finite(self.y, "y"))
        object.__setattr__(self, "z", _finite(self.z, "z"))

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceMM:
    min_x: float = -500.0
    max_x: float = 500.0
    min_y: float = -500.0
    max_y: float = 500.0
    min_z: float = 0.0
    max_z: float = 600.0

    def contains(self, pose: PoseMM) -> bool:
        return (
            self.min_x <= pose.x <= self.max_x
            and self.min_y <= pose.y <= self.max_y
            and self.min_z <= pose.z <= self.max_z
        )


@dataclass
class RobotStep:
    action: str
    frame: CoordinateFrame | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    dx: float | None = None
    dy: float | None = None
    dz: float | None = None
    seconds: float | None = None

    def __post_init__(self) -> None:
        self.action = str(self.action).strip().casefold()
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError(f"unknown action: {self.action}")
        if self.frame is not None and not isinstance(self.frame, CoordinateFrame):
            try:
                self.frame = CoordinateFrame(self.frame)
            except ValueError as error:
                raise ValueError(f"unknown coordinate frame: {self.frame}") from error
        for name in ("x", "y", "z", "dx", "dy", "dz", "seconds"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, _finite(value, name))

        required = {
            "move": {"x", "y", "z", "frame"},
            "move_xy": {"x", "y", "frame"},
            "rel": set(),
            "wait": set(),
            "open": set(),
            "close": set(),
            "pose": set(),
            "stop": set(),
        }[self.action]
        for name in required:
            if getattr(self, name) is None:
                raise ValueError(f"{name} is required for {self.action}")
        allowed = {
            "move": {"x", "y", "z", "frame"},
            "move_xy": {"x", "y", "frame"},
            "rel": {"dx", "dy", "dz"},
            "wait": {"seconds"},
            "open": set(),
            "close": set(),
            "pose": set(),
            "stop": set(),
        }[self.action]
        fields = {"frame", "x", "y", "z", "dx", "dy", "dz", "seconds"}
        unexpected = [name for name in fields - allowed if getattr(self, name) is not None]
        if unexpected:
            raise ValueError(f"unexpected fields for {self.action}: {', '.join(sorted(unexpected))}")
        if self.action == "rel" and self.dx is None and self.dy is None and self.dz is None:
            raise ValueError("at least one relative delta is required")
        if self.action == "wait" and self.seconds is None:
            self.seconds = 1.0

    @classmethod
    def from_dict(cls, data: Any) -> "RobotStep":
        allowed = {"action", "frame", "x", "y", "z", "dx", "dy", "dz", "seconds"}
        value = _strict_object(data, allowed, {"action"})
        return cls(**value)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"action": self.action}
        for name in ("frame", "x", "y", "z", "dx", "dy", "dz", "seconds"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value.value if isinstance(value, Enum) else value
        return result


@dataclass
class RobotTask:
    task_id: str
    steps: list[RobotStep]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", self.task_id):
            raise ValueError("invalid task_id")
        if not 1 <= len(self.steps) <= 32:
            raise ValueError("steps must contain between 1 and 32 items")
        if any(step.action == "stop" for step in self.steps) and len(self.steps) != 1:
            raise ValueError("stop must be a standalone task")

    @classmethod
    def from_dict(cls, data: Any) -> "RobotTask":
        value = _strict_object(data, {"task_id", "steps"}, {"task_id", "steps"})
        if not isinstance(value["task_id"], str):
            raise ValueError("task_id must be a string")
        if not isinstance(value["steps"], list):
            raise ValueError("steps must be a list")
        return cls(value["task_id"], [RobotStep.from_dict(step) for step in value["steps"]])

    def to_dict(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "steps": [step.to_dict() for step in self.steps]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class DirectStop:
    action: str = "stop"
    task_id: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "DirectStop":
        value = _strict_object(data, {"action", "task_id"}, {"action"})
        if value["action"] != "stop":
            raise ValueError("not a stop command")
        task_id = value.get("task_id")
        if task_id is not None:
            if not isinstance(task_id, str) or len(task_id) > 100:
                raise ValueError("task_id must be a string up to 100 characters")
        return cls(task_id=task_id)


@dataclass
class RobotStatus:
    task_id: str | None
    state: str
    step: int | None = None
    total_steps: int | None = None
    message: str | None = None

    @classmethod
    def from_json(cls, raw: str) -> "RobotStatus":
        data = _strict_object(
            json.loads(raw), {"task_id", "state", "step", "total_steps", "message"}, {"state"}
        )
        if not isinstance(data["state"], str):
            raise ValueError("state must be a string")
        task_id = data.get("task_id")
        if task_id is not None and not isinstance(task_id, str):
            raise ValueError("task_id must be a string or null")
        return cls(
            task_id=task_id,
            state=data["state"],
            step=data.get("step"),
            total_steps=data.get("total_steps"),
            message=data.get("message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class CoordinatesM:
    frame_id: str
    x: float
    y: float
    z: float

    @classmethod
    def from_dict(cls, data: Any) -> "CoordinatesM":
        value = _strict_object(data, {"frame_id", "x", "y", "z"}, {"frame_id", "x", "y", "z"})
        return cls(
            frame_id=str(value["frame_id"]),
            x=_finite(value["x"], "x"),
            y=_finite(value["y"], "y"),
            z=_finite(value["z"], "z"),
        )


@dataclass(frozen=True)
class HighLevelPlan:
    behavior: str
    object: str | None
    destination: str | None
    coordinates: CoordinatesM | None
    requires_confirmation: bool

    @classmethod
    def from_dict(cls, data: Any) -> "HighLevelPlan":
        value = _strict_object(
            data,
            {"behavior", "arguments", "requires_confirmation", "clarification_question"},
            {"behavior", "arguments", "requires_confirmation"},
        )
        behavior = str(value["behavior"])
        if behavior not in ALLOWED_BEHAVIORS:
            raise ValueError(f"unknown behavior: {behavior}")
        arguments = _strict_object(
            value["arguments"], {"object", "destination", "coordinates"}
        )
        coordinates_raw = arguments.get("coordinates")
        coordinates = CoordinatesM.from_dict(coordinates_raw) if coordinates_raw is not None else None
        object_name = arguments.get("object")
        destination = arguments.get("destination")
        if object_name is not None and not isinstance(object_name, str):
            raise ValueError("object must be a string or null")
        if destination is not None and not isinstance(destination, str):
            raise ValueError("destination must be a string or null")
        if behavior in {"pick", "pick_and_place"} and not object_name:
            raise ValueError("object is required")
        if behavior in {"place", "pick_and_place"} and not destination:
            raise ValueError("destination is required")
        if behavior == "move_to" and coordinates is None:
            raise ValueError("coordinates are required")
        requires_confirmation = value["requires_confirmation"]
        if not isinstance(requires_confirmation, bool):
            raise ValueError("requires_confirmation must be boolean")
        return cls(behavior, object_name, destination, coordinates, requires_confirmation)


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    plan: HighLevelPlan

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionRequest":
        value = _strict_object(data, {"request_id", "plan"}, {"request_id", "plan"})
        request_id = value["request_id"]
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError("request_id must be a non-empty string up to 100 characters")
        return cls(request_id=request_id, plan=HighLevelPlan.from_dict(value["plan"]))


def canonical_detection_name(class_name: str, color: str) -> str:
    value = "_".join(part for part in (color.strip(), class_name.strip()) if part)
    return re.sub(r"[^\w]+", "_", value.casefold(), flags=re.UNICODE).strip("_")


def build_pick_steps(object_pose: PoseMM, lift_mm: float) -> list[RobotStep]:
    return [
        RobotStep(action="open"),
        RobotStep(
            action="move",
            frame=CoordinateFrame.CAMERA,
            x=object_pose.x,
            y=object_pose.y,
            z=object_pose.z,
        ),
        RobotStep(action="close"),
        RobotStep(action="rel", dx=0.0, dy=0.0, dz=lift_mm),
    ]


def build_place_steps(destination_pose: PoseMM) -> list[RobotStep]:
    return [
        RobotStep(
            action="move_xy",
            frame=CoordinateFrame.CAMERA,
            x=destination_pose.x,
            y=destination_pose.y,
        ),
        RobotStep(
            action="move",
            frame=CoordinateFrame.CAMERA,
            x=destination_pose.x,
            y=destination_pose.y,
            z=destination_pose.z,
        ),
        RobotStep(action="open"),
    ]
