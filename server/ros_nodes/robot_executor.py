"""Validated executor for the existing SO101Arm and ROS String topic contract."""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from typing import Any

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from so101_control import SO101Arm
from so101_protocol import (
    COMMAND_TOPIC,
    STATUS_TOPIC,
    CoordinateFrame,
    DirectStop,
    PoseMM,
    RobotStatus,
    RobotStep,
    RobotTask,
    WorkspaceMM,
)


class TaskCancelled(RuntimeError):
    pass


class RobotExecutor(Node):
    def __init__(self) -> None:
        super().__init__("robot_executor")
        self.move_tolerance_mm = float(
            self.declare_parameter("move_tolerance_mm", 30.0).value
        )
        self.relative_tolerance_mm = float(
            self.declare_parameter("relative_tolerance_mm", 30.0).value
        )
        self.max_relative_mm = float(
            self.declare_parameter("max_relative_mm", 300.0).value
        )
        self.workspace = WorkspaceMM(
            min_x=float(self.declare_parameter("workspace.min_x", -500.0).value),
            max_x=float(self.declare_parameter("workspace.max_x", 500.0).value),
            min_y=float(self.declare_parameter("workspace.min_y", -500.0).value),
            max_y=float(self.declare_parameter("workspace.max_y", 500.0).value),
            min_z=float(self.declare_parameter("workspace.min_z", 0.0).value),
            max_z=float(self.declare_parameter("workspace.max_z", 600.0).value),
        )

        self.get_logger().info("Initializing SO101Arm...")
        self.arm = SO101Arm()
        self.get_logger().info("SO101Arm initialized")

        self.status_pub = self.create_publisher(String, STATUS_TOPIC, 10)
        self.command_sub = self.create_subscription(
            String, COMMAND_TOPIC, self.command_callback, 10
        )
        self.command_queue: queue.Queue[tuple[RobotTask, int]] = queue.Queue(maxsize=10)
        self.state_lock = threading.Lock()
        self.cancel_epoch = 0
        self.active_task_id: str | None = None
        self.shutdown_event = threading.Event()
        self.worker_thread = threading.Thread(target=self.worker, daemon=True)
        self.worker_thread.start()
        self.get_logger().info(
            f"Listening on {COMMAND_TOPIC}; coordinates are passed through without offsets"
        )

    def command_callback(self, msg: String) -> None:
        try:
            payload = json.loads(msg.data)
            if not isinstance(payload, dict):
                raise ValueError("command must be a JSON object")
            if payload.get("action") == "stop":
                stop = DirectStop.from_dict(payload)
                self.stop_all(stop.task_id)
                return
            task = RobotTask.from_dict(payload)
        except (json.JSONDecodeError, ValueError) as error:
            self.get_logger().error(f"Rejected command: {error}")
            self.publish_status(None, "rejected", message=str(error))
            return

        if task.steps[0].action == "stop":
            self.stop_all(task.task_id)
            return

        with self.state_lock:
            epoch = self.cancel_epoch
        try:
            self.command_queue.put_nowait((task, epoch))
            self.publish_status(task.task_id, "queued", total_steps=len(task.steps))
        except queue.Full:
            self.publish_status(task.task_id, "rejected", message="command queue is full")

    def stop_all(self, stop_task_id: str | None) -> None:
        with self.state_lock:
            self.cancel_epoch += 1
            active = self.active_task_id
        cancelled_queued: list[str] = []
        while True:
            try:
                task, _ = self.command_queue.get_nowait()
            except queue.Empty:
                break
            cancelled_queued.append(task.task_id)
            self.command_queue.task_done()

        try:
            self.arm.stop()
            message = f"active={active}, queued={cancelled_queued}"
            self.publish_status(stop_task_id, "stopped", message=message)
        except Exception as error:
            self.publish_status(stop_task_id, "error", message=f"stop failed: {error}")

    def worker(self) -> None:
        while rclpy.ok() and not self.shutdown_event.is_set():
            try:
                task, epoch = self.command_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.execute_task(task, epoch)
            except TaskCancelled as error:
                self.publish_status(task.task_id, "cancelled", message=str(error))
            except Exception as error:
                self.get_logger().error(f"Task {task.task_id} failed: {error}")
                self.publish_status(task.task_id, "error", message=str(error))
            finally:
                with self.state_lock:
                    if self.active_task_id == task.task_id:
                        self.active_task_id = None
                self.command_queue.task_done()

    def execute_task(self, task: RobotTask, epoch: int) -> None:
        self.ensure_not_cancelled(epoch)
        with self.state_lock:
            self.active_task_id = task.task_id
        total = len(task.steps)
        self.publish_status(task.task_id, "running", step=0, total_steps=total)
        for index, step in enumerate(task.steps, start=1):
            self.ensure_not_cancelled(epoch)
            self.publish_status(task.task_id, "running", step=index, total_steps=total)
            self.execute_step(step, epoch)
        self.ensure_not_cancelled(epoch)
        self.publish_status(task.task_id, "done", step=total, total_steps=total)

    def ensure_not_cancelled(self, epoch: int) -> None:
        with self.state_lock:
            cancelled = epoch != self.cancel_epoch
        if cancelled or self.shutdown_event.is_set():
            raise TaskCancelled("task was stopped")

    def execute_step(self, step: RobotStep, epoch: int) -> None:
        action = step.action
        if action == "move":
            target = PoseMM(x=step.x, y=step.y, z=step.z)
            self.validate_workspace(target)
            result = self.arm.move(target.x, target.y, target.z)
            self.ensure_not_cancelled(epoch)
            self.verify_motion(result, target, self.move_tolerance_mm, "MOVE")
            return

        if action == "move_xy":
            before = self.pose()
            target = PoseMM(x=step.x, y=step.y, z=before.z)
            self.validate_workspace(target)
            result = self.arm.move(target.x, target.y, target.z)
            self.ensure_not_cancelled(epoch)
            self.verify_motion(result, target, self.move_tolerance_mm, "MOVE_XY")
            return

        if action == "rel":
            dx, dy, dz = step.dx or 0.0, step.dy or 0.0, step.dz or 0.0
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if distance > self.max_relative_mm:
                raise ValueError(f"relative move {distance:.1f} mm exceeds configured limit")
            before = self.pose()
            target = PoseMM(x=before.x + dx, y=before.y + dy, z=before.z + dz)
            self.validate_workspace(target)
            result = self.arm.move_relative(dx, dy, dz)
            self.ensure_not_cancelled(epoch)
            self.verify_motion(result, target, self.relative_tolerance_mm, "REL")
            return

        if action == "open":
            self.require_success(self.arm.open(), "OPEN")
            return
        if action == "close":
            self.require_success(self.arm.close(), "CLOSE")
            return
        if action == "pose":
            pose = self.pose()
            self.get_logger().info(f"POSE x={pose.x:.1f} y={pose.y:.1f} z={pose.z:.1f}")
            return
        if action == "wait":
            deadline = time.monotonic() + min(max(step.seconds or 1.0, 0.0), 10.0)
            while time.monotonic() < deadline:
                self.ensure_not_cancelled(epoch)
                remaining = max(0.0, deadline - time.monotonic())
                time.sleep(min(0.05, remaining))
            return
        if action == "stop":
            raise TaskCancelled("standalone stop requested")
        raise ValueError(f"unsupported action: {action}")

    def pose(self) -> PoseMM:
        value = self.arm.get_pose()
        return PoseMM(x=float(value.x), y=float(value.y), z=float(value.z))

    def validate_workspace(self, target: PoseMM) -> None:
        if not self.workspace.contains(target):
            raise ValueError(f"target outside workspace: {target.to_dict()}")

    def verify_motion(self, result: Any, target: PoseMM, tolerance: float, name: str) -> None:
        after = self.pose()
        error = math.dist((after.x, after.y, after.z), (target.x, target.y, target.z))
        self.get_logger().info(
            f"{name} target=({target.x:.1f},{target.y:.1f},{target.z:.1f}) "
            f"actual=({after.x:.1f},{after.y:.1f},{after.z:.1f}) error={error:.1f} mm"
        )
        if bool(result) or error <= tolerance:
            return
        raise RuntimeError(f"{name} failed: {self.result_message(result)}; error={error:.1f} mm")

    def require_success(self, result: Any, name: str) -> None:
        if not bool(result):
            raise RuntimeError(f"{name} failed: {self.result_message(result)}")

    @staticmethod
    def result_message(result: Any) -> str:
        return str(getattr(result, "message", result))

    def publish_status(
        self,
        task_id: str | None,
        state: str,
        step: int | None = None,
        total_steps: int | None = None,
        message: str | None = None,
    ) -> None:
        if not rclpy.ok():
            return
        status = RobotStatus(
            task_id=task_id,
            state=state,
            step=step,
            total_steps=total_steps,
            message=message,
        )
        msg = String()
        msg.data = status.to_json()
        self.status_pub.publish(msg)

    def shutdown(self) -> None:
        self.shutdown_event.set()
        self.stop_all("shutdown")
        self.worker_thread.join(timeout=2.0)
        try:
            self.arm.shutdown()
        except Exception as error:
            self.get_logger().warning(f"arm shutdown failed: {error}")


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = RobotExecutor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        try:
            rclpy.try_shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()
