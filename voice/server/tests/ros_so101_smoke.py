"""Manual ROS smoke: fake CV frames must produce one validated SO-101 task."""

import json
import time
import urllib.request

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose


def detection(class_id: str, color: str, x: float, y: float, z: float) -> Detection2D:
    value = Detection2D()
    value.id = f"object_id=smoke:color={color}:distance_mm=500"
    result = ObjectHypothesisWithPose()
    result.hypothesis.class_id = class_id
    result.hypothesis.score = 0.95
    result.pose.pose.position.x = x
    result.pose.pose.position.y = y
    result.pose.pose.position.z = z
    value.results.append(result)
    return value


def submit_plan() -> dict:
    body = json.dumps(
        {
            "request_id": "ros-smoke",
            "plan": {
                "behavior": "pick_and_place",
                "arguments": {
                    "object": "blue_mouse",
                    "destination": "blue_traffic_light",
                    "coordinates": None,
                },
                "requires_confirmation": False,
                "clarification_question": None,
            },
        }
    ).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:18030/execute",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def main() -> None:
    accepted = submit_plan()
    if accepted.get("state") != "SAMPLING":
        raise RuntimeError(f"plan was not accepted for sampling: {accepted}")

    rclpy.init()
    node = Node("so101_bridge_smoke")
    publisher = node.create_publisher(Detection2DArray, "/detected_objects", 10)
    commands: list[dict] = []
    node.create_subscription(
        String,
        "/agent/robot_command",
        lambda msg: commands.append(json.loads(msg.data)),
        10,
    )
    frame = Detection2DArray()
    frame.detections = [
        detection("mouse", "blue", 0.10, 0.05, 0.12),
        detection("traffic light", "blue", 0.20, -0.05, 0.10),
    ]
    deadline = time.monotonic() + 8.0
    try:
        while time.monotonic() < deadline and not commands:
            publisher.publish(frame)
            rclpy.spin_once(node, timeout_sec=0.15)
        if not commands:
            raise TimeoutError("bridge did not publish a robot command")
        task = commands[0]
        steps = task["steps"]
        assert steps[1] == {
            "action": "move",
            "frame": "camera",
            "x": 100.0,
            "y": 50.0,
            "z": 120.0,
        }
        assert steps[5] == {
            "action": "move",
            "frame": "camera",
            "x": 200.0,
            "y": -50.0,
            "z": 100.0,
        }
        assert steps[-1] == {
            "action": "move",
            "frame": "robot",
            "x": -78.7,
            "y": 6.7,
            "z": 474.7,
        }
        print(json.dumps(task, ensure_ascii=False, indent=2))
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
