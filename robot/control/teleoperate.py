#!/home/jetson/venvs/lerobot/bin/python
from __future__ import annotations

import argparse

from lerobot.processor import make_default_processors
from lerobot.scripts.lerobot_teleoperate import teleop_loop

from common import (
    FOLLOWER_CALIBRATION,
    LEADER_CALIBRATION,
    make_follower,
    make_leader,
    validate_calibration,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Official-style LeRobot teleoperation for the seven-motor SO101E."
    )
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--duration", type=float, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validate_calibration(FOLLOWER_CALIBRATION, "follower")
    validate_calibration(LEADER_CALIBRATION, "leader")

    # Match the official lerobot-teleoperate path: no startup interpolation and
    # no max_relative_target clipping. The follower receives the leader's full
    # current target on every control cycle, including the first one.
    leader = make_leader()
    follower = make_follower(max_relative_target=None)
    processors = make_default_processors()

    try:
        print("Connecting SO101E leader and follower...")
        leader.connect()
        follower.connect()

        print("LIVE TELEOP - press Ctrl+C to stop and release follower torque.")
        teleop_loop(
            teleop=leader,
            robot=follower,
            fps=args.fps,
            teleop_action_processor=processors[0],
            robot_action_processor=processors[1],
            robot_observation_processor=processors[2],
            duration=args.duration,
        )
    except KeyboardInterrupt:
        print("\nStopping teleoperation...")
    finally:
        if leader.is_connected:
            leader.disconnect()
        if follower.is_connected:
            follower.disconnect()
        print("Follower torque is OFF.")


if __name__ == "__main__":
    main()
