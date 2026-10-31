# Adapted from the Hugging Face SmolVLA finetuning example:
# https://github.com/huggingface/lerobot/blob/main/src/lerobot/policies/smolvla/modeling_smolvla.py
# Copyright 2025 HuggingFace Inc. team. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Launch official LeRobot SmolVLA finetuning for a prepared SO101E dataset."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys

from prepare_training import CAMERA, JOINTS, read_json


def command(args, info):
    features = info["features"]
    for key in ("observation.state", "action"):
        if (features[key]["shape"] != [7]
                or features[key]["names"] != [j + ".pos" for j in JOINTS]):
            raise ValueError(f"Invalid seven-joint schema: {key}")
    if info["robot_type"] != "so101e_follower" or info["fps"] != 20:
        raise ValueError("Expected SO101E data at 20 Hz")
    if info["total_frames"] < 2 or info["total_episodes"] < 1:
        raise ValueError("Empty training dataset")
    if info["features"][CAMERA]["shape"] != [720, 1280, 3]:
        raise ValueError("Unexpected front camera shape")
    # Discard the base checkpoint's camera and state schema. LeRobot derives
    # front RGB + 7D state/action from OUR dataset. Keep the padded 32D network
    # projections from the pretrained model; do not change max_*_dim to seven.
    return [sys.executable, "-m", "lerobot.scripts.lerobot_train",
        "--policy.path=lerobot/smolvla_base", "--policy.input_features={}",
        "--policy.output_features={}", "--policy.push_to_hub=false",
        "--policy.device=cuda", "--policy.empty_cameras=0", "--policy.adapt_to_pi_aloha=false",
        "--policy.use_delta_joint_actions_aloha=false",
        f"--dataset.repo_id={args.repo_id}", f"--dataset.root={args.dataset.resolve()}",
        "--dataset.video_backend=pyav", f"--output_dir={args.output.resolve()}",
        "--job_name=so101e_pick_place", f"--batch_size={args.batch_size}",
        f"--steps={args.steps}", f"--save_freq={args.save_freq}",
        f"--num_workers={args.num_workers}", "--seed=1000"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-id", default="local/so101e_pick_place")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--steps", type=int, default=20000)
    parser.add_argument("--save-freq", type=int, default=1000)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--run", action="store_true", help="Actually launch training; otherwise print only")
    args = parser.parse_args()
    if min(args.batch_size, args.steps, args.save_freq) <= 0 or args.num_workers < 0:
        parser.error("Batch size/steps/save frequency must be positive; workers nonnegative")
    if args.output.exists():
        parser.error("Output already exists. Choose a new run directory")
    sources = read_json(args.dataset / "training_sources.json")
    if sources.get("complete") is not True or sources.get("repo_id") != args.repo_id:
        parser.error("Dataset preparation incomplete, or repo-id differs")
    info = read_json(args.dataset / "meta/info.json")
    episodes = sources["episodes"]
    if (len(episodes) != info["total_episodes"] or not episodes
            or any(not ep.get("task", "").strip() for ep in episodes)):
        parser.error("Missing task labels or inconsistent provenance")
    cmd = command(args, info)
    print(shlex.join(cmd), flush=True)
    if args.run:
        import torch
        from lerobot.configs.train import TrainPipelineConfig
        if not torch.cuda.is_available():
            parser.error("CUDA is unavailable in this Python environment")
        # Avoid depending on the wandb -> trackio naming change in LeRobot.
        fields = TrainPipelineConfig.__dataclass_fields__
        for tracker in ("wandb", "trackio"):
            if tracker in fields:
                cmd.append(f"--{tracker}.enable=false")
        print(f"GPU: {torch.cuda.get_device_name(0)}", flush=True)
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
