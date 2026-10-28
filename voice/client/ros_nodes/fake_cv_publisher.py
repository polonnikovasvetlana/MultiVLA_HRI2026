"""Deterministic fake CV source for testing the ROS/HTTP robot pipeline.

The node only publishes perception messages.  It does not publish robot
commands and does not call the executor directly.
"""

from __future__ import annotations

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

from so101_protocol import DETECTION_TOPIC


def make_detection(
    object_id: int,
    class_id: str,
    color: str,
    score: float,
    x: float,
    y: float,
    z: float,
) -> Detection2D:
    """Build one detection; positions use metres, matching the real CV node."""
    detection = Detection2D()
    detection.id = (
        f"object_id={object_id}:color={color}:"
        f"distance_mm={(x * x + y * y + z * z) ** 0.5 * 1000.0:.1f}"
    )
    result = ObjectHypothesisWithPose()
    result.hypothesis.class_id = class_id
    result.hypothesis.score = score
    result.pose.pose.position.x = x
    result.pose.pose.position.y = y
    result.pose.pose.position.z = z
    result.pose.pose.orientation.w = 1.0
    detection.results.append(result)
    return detection


class FakeCVPublisher(Node):
    def __init__(self) -> None:
        super().__init__("fake_cv_publisher")
        self.publish_enabled = bool(
            self.declare_parameter("publish_enabled", False).value
        )
        self.frame_id = str(
            self.declare_parameter(
                "frame_id", "camera_color_optical_frame"
            ).value
        )
        rate_hz = float(self.declare_parameter("rate_hz", 10.0).value)
        confidence = float(self.declare_parameter("confidence", 0.95).value)
        if rate_hz <= 0.0:
            raise ValueError("rate_hz must be positive")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        self.mouse_xyz = (
            float(self.declare_parameter("mouse.x", 0.2125).value),
            float(self.declare_parameter("mouse.y", -0.0082).value),
            float(self.declare_parameter("mouse.z", 0.0190).value),
        )
        self.target_xyz = (
            float(self.declare_parameter("target.x", 0.3000).value),
            float(self.declare_parameter("target.y", 0.1000).value),
            float(self.declare_parameter("target.z", 0.0500).value),
        )
        self.confidence = confidence
        self.publisher = self.create_publisher(
            Detection2DArray,
            DETECTION_TOPIC,
            # Match the real YOLO publisher. RELIABLE publishers are also
            # compatible with the bridge's BEST_EFFORT sensor-data subscriber.
            QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
        )
        self.create_timer(1.0 / rate_hz, self.publish_frame)

        if self.publish_enabled:
            self.get_logger().warning(
                "FAKE CV ENABLED: publishing blue_mouse and blue_traffic_light "
                f"to {DETECTION_TOPIC} at {rate_hz:.1f} Hz "
                f"(ROS_DOMAIN_ID={self.context.get_domain_id()})"
            )
        else:
            self.get_logger().warning(
                "Fake CV is DISARMED. Restart with "
                "--ros-args -p publish_enabled:=true to publish test detections. "
                f"Current ROS_DOMAIN_ID={self.context.get_domain_id()}."
            )

    def publish_frame(self) -> None:
        if not self.publish_enabled:
            return
        frame = Detection2DArray()
        frame.header.stamp = self.get_clock().now().to_msg()
        frame.header.frame_id = self.frame_id
        frame.detections = [
            make_detection(
                1,
                "mouse",
                "blue",
                self.confidence,
                *self.mouse_xyz,
            ),
            make_detection(
                2,
                "box",
                "blue",
                self.confidence,
                *self.target_xyz,
            ),
        ]
        for detection in frame.detections:
            detection.header = frame.header
        self.publisher.publish(frame)


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = FakeCVPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
