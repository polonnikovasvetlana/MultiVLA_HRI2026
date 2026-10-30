"""A taught, calibration-bound home pose and a bounded-speed joint trajectory."""
import hashlib
import json
import math
from pathlib import Path

from capture import atomic_json, utc_now
from hardware import JOINT_IDS


def calibration_hash(calibrations):
    return hashlib.sha256(json.dumps(calibrations, sort_keys=True).encode()).hexdigest()


def checked_positions(values):
    if not isinstance(values, list) or len(values) != len(JOINT_IDS):
        raise ValueError("В home должны быть положения всех семи моторов")
    result = [float(value) for value in values]
    for name, value in zip(JOINT_IDS, result):
        lo, hi = (0, 100) if name == "gripper" else (-100, 100)
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"Положение {name} выходит за калиброванный диапазон")
    return result


def leader_aligned(state, leader):
    return bool(state is not None and leader is not None and
                all(abs(a-b) <= (5 if name == "gripper" else 2)
                    for name, a, b in zip(JOINT_IDS, state, leader)))


class HomeStore:
    def __init__(self, path):
        self.path = Path(path)
        self.pose, self.error = None, None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if data.get("joint_order") != list(JOINT_IDS):
                    raise ValueError("Порядок суставов в home не совпадает с SO101E")
                data["positions"] = checked_positions(data["positions"])
                if not data.get("calibration_sha256"):
                    raise ValueError("В home отсутствует привязка к калибровке")
                self.pose = data
            except (ValueError, KeyError, OSError, TypeError) as exc:
                self.error = "Не удалось прочитать home: " + str(exc)

    def save(self, positions, calibrations):
        data = {"schema_version": 1, "robot_type": "so101e_follower",
                "joint_order": list(JOINT_IDS), "positions": checked_positions(list(positions)),
                "calibration_sha256": calibration_hash(calibrations), "saved_at_utc": utc_now()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(self.path, data)
        self.pose, self.error = data, None

    def target(self, calibrations):
        if not self.pose:
            raise ValueError("Сначала поставьте руку в стартовую позу и нажмите «Запомнить home»")
        if self.pose["calibration_sha256"] != calibration_hash(calibrations):
            raise ValueError("После смены калибровки нужно заново запомнить home")
        return self.pose["positions"][:]


class HomeMotion:
    # In normalized joint units; smoothstep peak speed is 1.5 * delta / duration.
    MAX_SPEED = 15.0
    MIN_DURATION = 2.0

    def __init__(self, start, target, started_at):
        self.start, self.target = checked_positions(list(start)), checked_positions(list(target))
        self.started_at = started_at
        distance = max(abs(a-b) for a, b in zip(self.start, self.target))
        self.duration = max(self.MIN_DURATION, 1.5 * distance / self.MAX_SPEED)

    def progress(self, now):
        return min(1.0, max(0.0, (now-self.started_at)/self.duration))

    def command(self, now):
        t = self.progress(now)
        alpha = t*t*(3-2*t)
        return [a+(b-a)*alpha for a, b in zip(self.start, self.target)]

    def reached(self, measured):
        return all(abs(a-b) <= (5 if name == "gripper" else 2)
                   for name, a, b in zip(JOINT_IDS, measured, self.target))
