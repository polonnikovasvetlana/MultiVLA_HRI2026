"""Validate SO101E captures and copy explicitly labeled episodes into a new dataset."""
import argparse
import copy
import importlib.metadata
import json
from pathlib import Path

JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex",
          "wrist_roll", "gripper", "elbow_rotate"]
CAMERA = "observation.images.front"
KEYS = ("observation.state", "action", CAMERA)
APPROACH = {
    "top": "Approach the object from above.",
    "left": "Approach the object from the left in the fixed camera view.",
    "right": "Approach the object from the right in the fixed camera view.",
}


def read_json(path):
    return json.loads(Path(path).read_text())


def validate_info(info):
    if info.get("robot_type") != "so101e_follower":
        raise ValueError("Expected robot_type=so101e_follower")
    if info.get("fps") != 20 or info.get("total_episodes") != 1:
        raise ValueError("Expected one episode at 20 Hz per capture")
    features = info["features"]
    names = [name + ".pos" for name in JOINTS]
    for key in KEYS[:2]:
        feature = features[key]
        if (list(feature["shape"]) != [7] or feature["names"] != names
                or feature["dtype"] != "float32"):
            raise ValueError(f"Unexpected joint order/type/shape: {key}")
    camera = features[CAMERA]
    if (list(camera["shape"]) != [720, 1280, 3] or camera["dtype"] != "video"
            or camera["names"] != ["height", "width", "channels"]):
        raise ValueError("Expected 720x1280 RGB front video")
    if {k for k, v in features.items() if v["dtype"] in ("video", "image")} != {CAMERA}:
        raise ValueError("Expected exactly one front camera")


def inspect_capture(manifest_path):
    manifest = read_json(manifest_path)
    name = manifest_path.name.removesuffix(".capture.json")
    root = manifest_path.parent / name  # Stored Jetson absolute paths are not portable.
    if manifest.get("id") != name:
        raise ValueError("Capture id does not match the adjacent directory")
    if manifest.get("demo") is not False:
        raise ValueError("Demo capture or missing demo flag")
    if manifest.get("state") != "saved" or manifest.get("error"):
        raise ValueError("Capture was not successfully saved")
    if manifest.get("joint_order") != JOINTS or manifest.get("fps") != 20:
        raise ValueError("Unexpected manifest joint order/fps")
    if manifest.get("approach_side") not in ("top", "left", "right"):
        raise ValueError("Missing approach side")
    if set(manifest.get("calibrations", {})) != {"leader", "follower"}:
        raise ValueError("Missing calibration snapshots")
    info = read_json(root / "meta/info.json")
    validate_info(info)
    if info["total_frames"] != manifest.get("frames") or info["total_frames"] < 2:
        raise ValueError("Empty capture or inconsistent frame count")
    previous_tick = None
    previous_sequence = None
    repeated, max_gap, count = 0, 0.0, 0
    with (root / "capture_timing.jsonl").open() as stream:
        for count, line in enumerate(stream, 1):
            timing = json.loads(line)
            if timing["frame_index"] != count - 1:
                raise ValueError("Timing frame indices are not consecutive")
            tick = timing["tick_ns"]
            if previous_tick is not None:
                if tick <= previous_tick:
                    raise ValueError("Non-monotonic control timestamps")
                max_gap = max(max_gap, (tick - previous_tick) / 1e9)
            sequence = timing["camera_sequence"]
            repeated += int(sequence == previous_sequence)
            previous_tick, previous_sequence = tick, sequence
    if count != info["total_frames"]:
        raise ValueError("Timing/frame count mismatch")
    return {"id": name, "root": root, "manifest": manifest, "info": info,
            "timing": {"repeated_camera_frames": repeated, "max_tick_gap_s": max_gap}}


def select_captures(source, labels):
    if not isinstance(labels, dict) or not labels:
        raise ValueError("Labels must be a non-empty JSON object: capture id -> task text")
    captures = []
    for name, task in labels.items():
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid capture id")
        if not isinstance(task, str) or not task.strip():
            raise ValueError(f"Empty task for {name}")
        item = inspect_capture(source / (name + ".capture.json"))
        item["task"] = task.strip() + " " + APPROACH[item["manifest"]["approach_side"]]
        captures.append(item)
    first = captures[0]
    for item in captures[1:]:
        if item["manifest"]["calibrations"] != first["manifest"]["calibrations"]:
            raise ValueError("Calibration differs between captures; inspect before combining")
        for key in KEYS:
            if item["info"]["features"][key] != first["info"]["features"][key]:
                raise ValueError(f"Incompatible feature metadata: {key}")
    return captures


def prepare(captures, output, repo_id, video_backend):
    # No hardware imports: this program only reads files and writes a separate dataset.
    import numpy as np
    from lerobot.datasets import LeRobotDataset
    from lerobot.configs.video import RGBEncoderConfig

    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    features = {key: copy.deepcopy(captures[0]["info"]["features"][key]) for key in KEYS}
    features[CAMERA].pop("info", None)  # Encoder will generate fresh video metadata.
    dataset = LeRobotDataset.create(
        repo_id, fps=20, root=output, features=features, robot_type="so101e_follower",
        streaming_encoding=True, encoder_threads=2,
        rgb_encoder=RGBEncoderConfig(vcodec="h264", preset="ultrafast", crf=23))
    provenance = {"complete": False, "repo_id": repo_id, "joint_order": JOINTS,
                  "camera": CAMERA, "lerobot_version": importlib.metadata.version("lerobot"),
                  "episodes": []}
    report = output / "training_sources.json"
    report.write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    try:
        for episode_index, item in enumerate(captures):
            source = LeRobotDataset("local/" + item["id"], root=item["root"],
                                    video_backend=video_backend)
            if len(source) != item["info"]["total_frames"]:
                raise ValueError(f"Reader frame count mismatch: {item['id']}")
            print(f"Copying {item['id']}: {len(source)} frames", flush=True)
            for index in range(len(source)):
                frame = source[index]
                values = {}
                for key in KEYS[:2]:
                    array = frame[key].detach().cpu().numpy().astype(np.float32)
                    if array.shape != (7,) or not np.isfinite(array).all():
                        raise ValueError(f"Invalid joint values: {item['id']}/{index}/{key}")
                    values[key] = array
                image = frame[CAMERA].detach().cpu().numpy()
                if image.shape != (3, 720, 1280) or not np.isfinite(image).all():
                    raise ValueError(f"Invalid RGB frame: {item['id']}/{index}")
                if image.dtype != np.uint8:
                    if image.min() < 0 or image.max() > 1:
                        raise ValueError("Expected float RGB values in [0, 1]")
                    image = np.rint(image * 255).astype(np.uint8)
                values[CAMERA] = np.ascontiguousarray(image.transpose(1, 2, 0))
                dataset.add_frame({**values, "task": item["task"]})
            dataset.save_episode()
            provenance["episodes"].append({"episode_index": episode_index,
                "source_id": item["id"], "task": item["task"],
                "capture": item["manifest"], "timing_summary": item["timing"]})
        dataset.finalize()
        provenance["complete"] = True
        report.write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    finally:
        dataset.finalize()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--labels", type=Path, help="Only ids listed in this JSON are selected")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo-id", default="local/so101e_pick_place")
    parser.add_argument("--video-backend", default="pyav")
    args = parser.parse_args()
    if not args.source.is_dir():
        parser.error("Source directory does not exist")
    if args.output is None:
        manifests = sorted(args.source.glob("*.capture.json"))
        if not manifests:
            parser.error("No *.capture.json files found; copy the adjacent manifests too")
        for path in manifests:
            try:
                item = inspect_capture(path)
                print(json.dumps({"id": item["id"], "frames": item["info"]["total_frames"],
                    "approach_side": item["manifest"]["approach_side"], **item["timing"]}))
            except (ValueError, KeyError, OSError) as exc:
                print(f"REJECT {path.name}: {exc}")
        return
    if args.labels is None:
        parser.error("--output requires --labels; task labels must be supplied explicitly")
    captures = select_captures(args.source.resolve(), read_json(args.labels))
    prepare(captures, args.output.resolve(), args.repo_id, args.video_backend)


if __name__ == "__main__":
    main()
