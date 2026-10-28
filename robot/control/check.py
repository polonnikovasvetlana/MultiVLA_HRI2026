#!/home/jetson/venvs/lerobot/bin/python
from __future__ import annotations

from common import FOLLOWER_PORT, LEADER_PORT, check_bus


def main() -> None:
    check_bus("FOLLOWER", FOLLOWER_PORT)
    check_bus("LEADER", LEADER_PORT)
    print("\nOK: both SO101E buses contain IDs 1-7.")


if __name__ == "__main__":
    main()
