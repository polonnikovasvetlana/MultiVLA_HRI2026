from __future__ import annotations

import json
from pathlib import Path

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus
from lerobot_robot_so101e import SO101EFollower, SO101EFollowerConfig
from lerobot_teleoperator_so101e_leader import SO101ELeader, SO101ELeaderConfig


FOLLOWER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970072412-if00"
LEADER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970073650-if00"

FOLLOWER_ID = "so101e_follower"
LEADER_ID = "so101e_leader"

JOINT_IDS = {
    "shoulder_pan": 1,
    "shoulder_lift": 2,
    "elbow_flex": 3,
    "wrist_flex": 4,
    "wrist_roll": 5,
    "gripper": 6,
    "elbow_rotate": 7,
}

CALIBRATION_ROOT = Path.home() / ".cache/huggingface/lerobot/calibration"
FOLLOWER_CALIBRATION = (
    CALIBRATION_ROOT / "robots/so101e_follower" / f"{FOLLOWER_ID}.json"
)
LEADER_CALIBRATION = (
    CALIBRATION_ROOT / "teleoperators/so101e_leader" / f"{LEADER_ID}.json"
)


def make_probe_bus(port: str) -> FeetechMotorsBus:
    body_mode = MotorNormMode.RANGE_M100_100
    motors = {
        name: Motor(
            motor_id,
            "sts3215",
            MotorNormMode.RANGE_0_100 if name == "gripper" else body_mode,
        )
        for name, motor_id in JOINT_IDS.items()
    }
    return FeetechMotorsBus(port=port, motors=motors)


def check_bus(label: str, port: str) -> None:
    bus = make_probe_bus(port)
    try:
        bus.connect()
        print(f"\n{label}: {port}")
        for name, expected_id in JOINT_IDS.items():
            model = bus.ping(expected_id, num_retry=5, raise_on_error=True)
            torque = bus.read("Torque_Enable", name, normalize=False, num_retry=5)
            voltage = bus.read("Present_Voltage", name, normalize=False, num_retry=5)
            temperature = bus.read("Present_Temperature", name, normalize=False, num_retry=5)
            print(
                f"  {expected_id} {name:16} model={model} "
                f"torque={torque} voltage={voltage / 10:.1f}V temp={temperature}C"
            )
    finally:
        if bus.is_connected:
            bus.disconnect(disable_torque=False)


def validate_calibration(path: Path, role: str) -> None:
    if not path.is_file():
        raise SystemExit(
            f"No {role} calibration: {path}\n"
            f"Run: python ~/so101e_tools/calibrate.py {role}"
        )

    data = json.loads(path.read_text())
    if set(data) != set(JOINT_IDS):
        raise SystemExit(
            f"Invalid {role} calibration joints in {path}: "
            f"expected {sorted(JOINT_IDS)}, got {sorted(data)}"
        )

    for name, expected_id in JOINT_IDS.items():
        item = data[name]
        if item.get("id") != expected_id:
            raise SystemExit(
                f"Invalid {role} calibration: {name} has ID {item.get('id')}, "
                f"expected {expected_id}"
            )
        if item.get("range_min") is None or item.get("range_max") is None:
            raise SystemExit(f"Invalid {role} calibration range for {name}")
        if item["range_min"] >= item["range_max"]:
            raise SystemExit(f"Empty {role} calibration range for {name}")


def make_follower(max_relative_target: float | None = 3.0) -> SO101EFollower:
    return SO101EFollower(
        SO101EFollowerConfig(
            port=FOLLOWER_PORT,
            id=FOLLOWER_ID,
            max_relative_target=max_relative_target,
        )
    )


def make_leader() -> SO101ELeader:
    return SO101ELeader(SO101ELeaderConfig(port=LEADER_PORT, id=LEADER_ID))


def read_normalized_positions(device) -> dict[str, float]:
    try:
        device.bus.connect()
        return device.bus.sync_read("Present_Position", num_retry=5)
    finally:
        if device.bus.is_connected:
            device.bus.disconnect(disable_torque=False)
