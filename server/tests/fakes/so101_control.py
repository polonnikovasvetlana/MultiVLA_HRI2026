"""Fake SO101Arm used only by the manual ROS executor smoke test."""

from dataclasses import dataclass


@dataclass
class Pose:
    x: float = 0.0
    y: float = 0.0
    z: float = 100.0


class Result:
    def __init__(self, success: bool = True, message: str = "ok") -> None:
        self.success = success
        self.message = message

    def __bool__(self) -> bool:
        return self.success


class SO101Arm:
    def __init__(self) -> None:
        self.pose = Pose()

    def move(self, x: float, y: float, z: float) -> Result:
        self.pose = Pose(x, y, z)
        return Result()

    def move_relative(self, dx: float, dy: float, dz: float) -> Result:
        self.pose = Pose(self.pose.x + dx, self.pose.y + dy, self.pose.z + dz)
        return Result()

    def get_pose(self) -> Pose:
        return self.pose

    def open(self) -> Result:
        return Result()

    def close(self) -> Result:
        return Result()

    def stop(self) -> Result:
        return Result()

    def shutdown(self) -> None:
        return None
