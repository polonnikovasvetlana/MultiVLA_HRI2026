#!/home/jetson/venvs/lerobot/bin/python
from __future__ import annotations

import argparse

from lerobot.motors import MotorCalibration
from lerobot.motors.feetech import OperatingMode

from common import (
    FOLLOWER_CALIBRATION,
    FOLLOWER_PORT,
    LEADER_CALIBRATION,
    LEADER_PORT,
    check_bus,
    make_follower,
    make_leader,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate the seven-motor SO101E without modifying stock SO101 files."
    )
    parser.add_argument("role", nargs="?", choices=("leader", "follower", "both"))
    return parser.parse_args()


def calibrate_device(device, calibration_path) -> None:
    if device.calibration:
        answer = input(
            f"Calibration already exists at {calibration_path}. "
            "Press ENTER to reuse it, or type 'c' to recalibrate: "
        )
        if answer.strip().lower() != "c":
            device.bus.write_calibration(device.calibration)
            print(f"Reused calibration: {calibration_path}")
            return

    device.bus.disable_torque()
    for motor in device.bus.motors:
        device.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

    input("Move every joint to the middle of its safe range, then press ENTER...")
    homing_offsets = device.bus.set_half_turn_homings()

    full_turn_motor = "wrist_roll"
    measured_motors = [name for name in device.bus.motors if name != full_turn_motor]
    print(
        "Move these joints one at a time through their full SAFE ranges:\n"
        + ", ".join(measured_motors)
        + "\nDo not twist elbow_rotate farther than its cable permits. "
        "Press ENTER when finished..."
    )
    range_mins, range_maxes = device.bus.record_ranges_of_motion(measured_motors)
    range_mins[full_turn_motor] = 0
    range_maxes[full_turn_motor] = 4095

    device.calibration = {
        name: MotorCalibration(
            id=motor.id,
            drive_mode=0,
            homing_offset=homing_offsets[name],
            range_min=range_mins[name],
            range_max=range_maxes[name],
        )
        for name, motor in device.bus.motors.items()
    }
    device.bus.write_calibration(device.calibration)
    device._save_calibration()
    print(f"Calibration saved: {calibration_path}")


def calibrate_role(role: str) -> None:
    if role == "leader":
        check_bus("LEADER", LEADER_PORT)
        device = make_leader()
        path = LEADER_CALIBRATION
    else:
        check_bus("FOLLOWER", FOLLOWER_PORT)
        device = make_follower()
        path = FOLLOWER_CALIBRATION

    print(f"\nCalibration file: {path}")
    device.bus.connect()
    try:
        calibrate_device(device, path)
    finally:
        if device.bus.is_connected:
            device.bus.disconnect()


def main() -> None:
    args = parse_args()
    role = args.role
    if role is None:
        print("Choose what to calibrate:")
        print("  1 - leader")
        print("  2 - follower")
        print("  3 - both (leader first)")
        choice = input("Selection [1/2/3]: ").strip()
        role = {"1": "leader", "2": "follower", "3": "both"}.get(choice)
        if role is None:
            raise SystemExit("Invalid selection. Run the program again and choose 1, 2, or 3.")

    roles = ("leader", "follower") if role == "both" else (role,)
    for index, selected_role in enumerate(roles):
        if index:
            input("\nLeader is complete. Prepare the follower, then press ENTER...")
        calibrate_role(selected_role)


if __name__ == "__main__":
    main()
