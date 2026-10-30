"""SO101E configuration and the hardware boundary used by the recorder."""
from pathlib import Path
import json
import subprocess
import time

FOLLOWER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970072412-if00"
LEADER_PORT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970073650-if00"
JOINT_IDS = dict(shoulder_pan=1, shoulder_lift=2, elbow_flex=3, wrist_flex=4,
                 wrist_roll=5, gripper=6, elbow_rotate=7)
CALIBRATION_ROOT = Path.home() / ".cache/huggingface/lerobot/calibration"
CALIBRATIONS = {
    "follower": CALIBRATION_ROOT / "robots/so101e_follower/so101e_follower.json",
    "leader": CALIBRATION_ROOT / "teleoperators/so101e_leader/so101e_leader.json",
}


def calibration_snapshots():
    result = {}
    for role, path in CALIBRATIONS.items():
        data = json.loads(path.read_text())
        if set(data) != set(JOINT_IDS):
            raise ValueError(f"Калибровка {role}: ожидаются семь суставов SO101E")
        for name, motor_id in JOINT_IDS.items():
            item = data[name]
            if item.get("id") != motor_id or item["range_min"] >= item["range_max"]:
                raise ValueError(f"Некорректная калибровка {role}/{name}")
        result[role] = data
    return result


class Robot:
    def __init__(self):
        self.leader = self.follower = None
        self.calibrations = {}

    def connect(self):
        from lerobot_robot_so101e import SO101EFollower, SO101EFollowerConfig
        from lerobot_teleoperator_so101e_leader import SO101ELeader, SO101ELeaderConfig
        self.calibrations = calibration_snapshots()
        for port in (FOLLOWER_PORT, LEADER_PORT):
            if not Path(port).exists():
                raise RuntimeError(f"Не найден порт {port}")
        busy = subprocess.run(["fuser", FOLLOWER_PORT, LEADER_PORT], capture_output=True)
        if busy.returncode == 0:
            raise RuntimeError("Порты заняты. Остановите отдельный teleoperate.py")
        self.leader = SO101ELeader(SO101ELeaderConfig(port=LEADER_PORT, id="so101e_leader"))
        self.follower = SO101EFollower(SO101EFollowerConfig(
            port=FOLLOWER_PORT, id="so101e_follower", cameras={}, max_relative_target=None))
        self.leader.connect(calibrate=False)
        self.follower.connect(calibrate=False)
        if not self.leader.bus.is_calibrated or not self.follower.bus.is_calibrated:
            raise RuntimeError("Калибровка в моторах не совпадает с файлами")

    def sample(self):
        state_ns = time.monotonic_ns()
        state = self.read_state()
        sent = self.send_target(self.read_leader())
        return state, sent, state_ns, time.monotonic_ns()

    def read_state(self):
        state = self.follower.get_observation()
        return [state[name + ".pos"] for name in JOINT_IDS]

    def read_leader(self):
        action = self.leader.get_action()
        return [action[name + ".pos"] for name in JOINT_IDS]

    def send_target(self, positions):
        action = {name + ".pos": value for name, value in zip(JOINT_IDS, positions)}
        sent = self.follower.send_action(action)
        return [sent[name + ".pos"] for name in JOINT_IDS]

    def close(self):
        errors = []
        for device in (self.follower, self.leader):
            if device is not None and device.bus.is_connected:
                try:
                    device.bus.disconnect(disable_torque=True)
                except Exception as exc:
                    errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))


class DemoRobot:
    """Explicit test mode. Never imports or opens motor drivers."""
    calibrations = {}

    def __init__(self):
        self.positions = [0, 0, 0, 0, 0, 50, 0]
        self.leader_positions = [15, -15, 10, -10, 5, 60, 8]

    def connect(self):
        pass

    def sample(self):
        tick = time.monotonic_ns()
        state = self.read_state()
        return state, self.send_target(self.read_leader()), tick, time.monotonic_ns()

    def read_state(self):
        return self.positions[:]

    def read_leader(self):
        return self.leader_positions[:]

    def send_target(self, positions):
        self.positions = list(positions)
        return self.positions[:]

    def close(self):
        pass
