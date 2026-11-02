"""Training preparation guards, without LeRobot, CUDA, video codecs or motors."""
import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_training as preparation
import train_smolvla as training


def info():
    vector = {"dtype": "float32", "shape": [7],
              "names": [j + ".pos" for j in preparation.JOINTS]}
    return {"robot_type": "so101e_follower", "fps": 20, "total_episodes": 1,
            "total_frames": 2, "features": {
                "observation.state": copy.deepcopy(vector), "action": copy.deepcopy(vector),
                preparation.CAMERA: {"dtype": "video", "shape": [720, 1280, 3],
                                    "names": ["height", "width", "channels"]}}}


class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.name = "so101e_example_left"
        self.capture = self.root / self.name
        (self.capture / "meta").mkdir(parents=True)
        (self.capture / "meta/info.json").write_text(json.dumps(info()))
        self.manifest_path = self.root / (self.name + ".capture.json")
        self.manifest = {"id": self.name, "demo": False, "state": "saved", "error": None,
            "joint_order": preparation.JOINTS, "fps": 20, "frames": 2,
            "approach_side": "left", "calibrations": {"leader": {}, "follower": {}},
            "path": "/old/jetson/path/does/not/exist"}
        self.write_manifest()
        self.write_timing([0, 50_000_000], [1, 1])

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def write_timing(self, ticks, sequences):
        (self.capture / "capture_timing.jsonl").write_text("".join(
            json.dumps({"frame_index": i, "tick_ns": tick, "camera_sequence": seq}) + "\n"
            for i, (tick, seq) in enumerate(zip(ticks, sequences))))

    def test_relocated_capture_and_duplicate_camera_frames(self):
        item = preparation.inspect_capture(self.manifest_path)
        self.assertEqual(item["root"], self.capture)
        self.assertEqual(item["timing"], {"repeated_camera_frames": 1, "max_tick_gap_s": .05})

    def test_demo_error_and_incomplete_captures_rejected(self):
        for key, value in [("demo", True), ("demo", None), ("state", "error"),
                           ("state", "recording"), ("error", "disk full"), ("frames", 3)]:
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.manifest)
                self.manifest[key] = value
                self.write_manifest()
                with self.assertRaises(ValueError):
                    preparation.inspect_capture(self.manifest_path)
                self.manifest = original

    def test_joint_order_must_match_not_just_dimension(self):
        data = info()
        data["features"]["action"]["names"].reverse()
        with self.assertRaises(ValueError):
            preparation.validate_info(data)

    def test_camera_and_robot_must_match(self):
        for key, value in [("fps", 30), ("robot_type", "so101_follower"), ("total_episodes", 2)]:
            data = info()
            data[key] = value
            with self.assertRaises(ValueError):
                preparation.validate_info(data)

    def test_timing_mismatch_and_nonmonotonic_rejected(self):
        for ticks in ([0], [1, 1], [2, 1]):
            self.write_timing(ticks, [1] * len(ticks))
            with self.assertRaises(ValueError):
                preparation.inspect_capture(self.manifest_path)

    def test_explicit_task_not_inferred_from_approach_side(self):
        task = "Pick up the object and place it on the object on the right."
        result = preparation.select_captures(self.root, {self.name: task})
        self.assertEqual(result[0]["task"], task + " " + preparation.APPROACH["left"])
        self.assertEqual(result[0]["manifest"]["approach_side"], "left")

    def test_empty_labels_and_path_traversal_rejected(self):
        for labels in ({}, {self.name: " "}, {"../escape": "move"}):
            with self.assertRaises(ValueError):
                preparation.select_captures(self.root, labels)

    def test_mixed_calibration_rejected(self):
        item = preparation.inspect_capture(self.manifest_path)
        different = copy.deepcopy(item)
        different["manifest"]["calibrations"]["follower"] = {"changed": True}
        with patch.object(preparation, "inspect_capture", side_effect=[item, different]):
            with self.assertRaises(ValueError):
                preparation.select_captures(self.root, {"a": "pick", "b": "place"})

    def test_launcher_uses_our_features_and_official_pretrained_model(self):
        args = argparse.Namespace(repo_id="local/test", dataset=self.capture,
            output=self.root / "run", batch_size=8, steps=20, save_freq=10, num_workers=0)
        cmd = training.command(args, info())
        self.assertTrue(any(value.endswith("train_with_mlflow.py") for value in cmd))
        self.assertIn("--mlflow-dir", cmd)
        self.assertIn("--policy.path=lerobot/smolvla_base", cmd)
        self.assertIn("--policy.input_features={}", cmd)
        self.assertIn("--policy.output_features={}", cmd)
        self.assertIn("--policy.push_to_hub=false", cmd)
        self.assertIn("--policy.device=cuda", cmd)
        self.assertFalse(any("max_action_dim=7" in value for value in cmd))


if __name__ == "__main__":
    unittest.main()
