"""Manual ROS smoke proving that immediate stop cancels an active task."""

import json
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


def main() -> None:
    rclpy.init()
    node = Node("robot_executor_stop_smoke")
    command_pub = node.create_publisher(String, "/agent/robot_command", 10)
    statuses: list[dict] = []
    node.create_subscription(
        String,
        "/robot_executor/status",
        lambda msg: statuses.append(json.loads(msg.data)),
        10,
    )

    deadline = time.monotonic() + 5.0
    while command_pub.get_subscription_count() < 1 and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    if command_pub.get_subscription_count() < 1:
        raise TimeoutError("robot_executor did not subscribe")

    task = String()
    task.data = json.dumps(
        {
            "task_id": "long-task",
            "steps": [
                {"action": "wait", "seconds": 5.0},
                {"action": "open"},
            ],
        }
    )
    command_pub.publish(task)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        if any(item.get("task_id") == "long-task" and item.get("state") == "running" for item in statuses):
            break
    else:
        raise TimeoutError("task did not start")

    stop = String()
    stop.data = json.dumps({"action": "stop", "task_id": "smoke-stop"})
    command_pub.publish(stop)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
        states = {(item.get("task_id"), item.get("state")) for item in statuses}
        if ("long-task", "cancelled") in states and ("smoke-stop", "stopped") in states:
            break
    else:
        raise TimeoutError(f"expected stop states not received: {statuses}")

    assert not any(
        item.get("task_id") == "long-task" and item.get("state") == "done" for item in statuses
    )
    print(json.dumps(statuses, ensure_ascii=False, indent=2))
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
