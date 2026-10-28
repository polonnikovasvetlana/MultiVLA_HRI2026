#!/usr/bin/env python3
"""Record a raw SO101E pilot using the installed LeRobot recorder."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys


FOLLOWER_PORT = '/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970072412-if00'
LEADER_PORT = '/dev/serial/by-id/usb-1a86_USB_Single_Serial_5970073650-if00'
FOLLOWER_ID = 'so101e_follower'
LEADER_ID = 'so101e_leader'
JOINT_IDS = {'shoulder_pan': 1, 'shoulder_lift': 2, 'elbow_flex': 3, 'wrist_flex': 4, 'wrist_roll': 5, 'gripper': 6, 'elbow_rotate': 7}
CALIBRATION_ROOT = Path.home() / ".cache/huggingface/lerobot/calibration"
FOLLOWER_CALIBRATION = CALIBRATION_ROOT / "robots/so101e_follower" / (FOLLOWER_ID + ".json")
LEADER_CALIBRATION = CALIBRATION_ROOT / "teleoperators/so101e_leader" / (LEADER_ID + ".json")

def validate_calibration(path: Path, role: str) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing {role} calibration: {path}")
    data = json.loads(path.read_text())
    if set(data) != set(JOINT_IDS):
        raise SystemExit(f"Expected seven SO101E joints in {path}")
    for name, motor_id in JOINT_IDS.items():
        item = data[name]
        if item.get("id") != motor_id:
            raise SystemExit(f"Invalid motor ID for {role}/{name}")
        lo, hi = item.get("range_min"), item.get("range_max")
        if lo is None or hi is None or lo >= hi:
            raise SystemExit(f"Invalid calibration range for {role}/{name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-object", required=True)
    parser.add_argument("--approach-side", choices=("top", "left", "right"), required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--num-episodes", type=int, default=3)
    parser.add_argument("--episode-time-s", type=float, default=30)
    parser.add_argument("--reset-time-s", type=float, default=15)
    parser.add_argument("--fps", type=int, default=20)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.num_episodes < 1 or not 1 <= args.fps <= 30:
        parser.error("Use a positive episode count and fps between 1 and 30.")
    if args.episode_time_s <= 0 or args.reset_time_s < 0:
        parser.error("Episode time must be positive; reset time cannot be negative.")
    if not args.target_object.strip() or not args.task.strip():
        parser.error("Object and task cannot be blank.")

    calibrations = {}
    for role, path in (("follower", FOLLOWER_CALIBRATION),
                       ("leader", LEADER_CALIBRATION)):
        validate_calibration(path, role)
        raw = path.read_bytes()
        calibrations[role] = {
            "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "values": json.loads(raw),
        }
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", args.target_object).strip("_") or "object"
    name = "so101e_{}_{}_{}".format(
        slug, args.approach_side, datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    root = (args.root or Path.home() / "datasets" / name).expanduser().resolve()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", root.name):
        parser.error("Dataset directory name must contain only letters, digits, _ or -.")
    repo_id = "local/" + root.name
    metadata_path = root.parent / (root.name + ".session.json")
    if root.exists() or metadata_path.exists():
        parser.error("Use a new dataset path for each session.")
    camera = "{front: {type: opencv, index_or_path: 0, width: 1280, height: 720, fps: 30, fourcc: MJPG}}"
    command = [
        sys.executable, "-m", "lerobot.scripts.lerobot_record",
        "--robot.type=so101e_follower", "--robot.port=" + FOLLOWER_PORT,
        "--robot.id=" + FOLLOWER_ID, "--robot.use_degrees=false",
        "--robot.cameras=" + camera,
        "--teleop.type=so101e_leader", "--teleop.port=" + LEADER_PORT,
        "--teleop.id=" + LEADER_ID, "--teleop.use_degrees=false",
        "--dataset.repo_id=" + repo_id, "--dataset.root=" + str(root),
        "--dataset.no_stamp=true", "--dataset.single_task=" + args.task,
        "--dataset.fps=" + str(args.fps),
        "--dataset.num_episodes=" + str(args.num_episodes),
        "--dataset.episode_time_s=" + str(args.episode_time_s),
        "--dataset.reset_time_s=" + str(args.reset_time_s),
        "--dataset.push_to_hub=false", "--dataset.streaming_encoding=true",
        "--dataset.rgb_encoder.vcodec=h264",
        "--dataset.rgb_encoder.preset=ultrafast",
        "--dataset.rgb_encoder.crf=23", "--dataset.encoder_threads=2",
        "--display_data=false", "--play_sounds=false",
    ]
    metadata = {
        "schema_version": 1, "kind": "raw_teleoperation",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_root": str(root), "repo_id": repo_id,
        "target_object": args.target_object, "approach_side": args.approach_side,
        "task": args.task, "fps": args.fps,
        "requested_episodes": args.num_episodes,
        "camera": {"key": "front", "width": 1280, "height": 720, "capture_fps": 30,
                   "fourcc": "MJPG", "output_color": "RGB"},
        "joint_order": list(JOINT_IDS),
        "joint_units": "[-100,100] for body; [0,100] for gripper",
        "visual_prompt": {"top": "red", "left": "blue", "right": "green",
                          "alpha": 0.35, "contour_px": 5},
        "calibrations": calibrations,
        "versions": {p: md.version(p) for p in (
            "lerobot", "lerobot_robot_so101e", "lerobot_teleoperator_so101e_leader",
            "torch", "av", "torchcodec")},
        "recorder_argv": command,
    }
    print("Dataset:", root, flush=True)
    print("Session metadata:", metadata_path, flush=True)
    print("Object/side:", args.target_object, args.approach_side, flush=True)
    print("Task:", args.task, flush=True)
    print("Command:", shlex.join(command), flush=True)
    if args.dry_run:
        print("Dry run: no hardware connected and no files written.")
        return 0

    devices = ["/dev/video0", FOLLOWER_PORT, LEADER_PORT]
    for device in devices:
        if not Path(device).exists():
            parser.error("Missing device: " + device)
    busy = subprocess.run(["fuser", *devices], capture_output=True, text=True)
    if busy.returncode == 0:
        parser.error("A camera or serial port is busy. Stop the existing sender/teleop first.")
    root.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("x") as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
    print("Starting live teleoperation: follower follows leader immediately.", flush=True)
    print("n/right: next; r/left: redo; q/Esc: finish. Keep this terminal focused.", flush=True)
    process = subprocess.Popen(command)
    # The child receives terminal Ctrl+C and must finish its own dataset cleanup.
    previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return process.wait()
    finally:
        signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
