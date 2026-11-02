"""Single-GPU LeRobot 0.6.1 entry point with a local MLflow logger adapter.

LeRobot routes scalar logs through its WandBLogger symbol. We replace that
symbol before main(), enabling the logging branch without constructing W&B.
No installed LeRobot files are modified. This adapter is version-specific.
"""
import argparse
from collections import Counter
import importlib.metadata
import json
import math
from pathlib import Path
import sys


def flatten(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            result.update(flatten(child, f"{prefix}.{key}" if prefix else key))
    else:
        result[prefix] = str(value)[:500]
    return result


def logger_class(mlflow):
    class MLflowLogger:
        def __init__(self, cfg):
            if mlflow.active_run() is None:
                raise RuntimeError("MLflow run must be started by train_with_mlflow.py")
            self.output = Path(cfg.output_dir)
            raw = cfg.to_dict()
            # Parameters from the resolved training config, not launcher defaults.
            config = {key: raw[key] for key in (
                "policy", "dataset", "optimizer", "scheduler", "seed", "batch_size",
                "steps", "num_workers", "save_freq", "log_freq", "eval_steps",
                "output_dir", "job_name", "accelerator") if key in raw}
            # Normalize Paths/enums to JSON values without changing cfg.
            config = json.loads(json.dumps(config, default=str))
            mlflow.log_dict(config, "config/resolved_train_config.json")
            params = flatten(config)
            entries = list(params.items())
            for offset in range(0, len(entries), 100):
                mlflow.log_params(dict(entries[offset:offset + 100]))
            root = Path(cfg.dataset.root)
            for name in ("training_sources.json", "meta/info.json"):
                path = root / name
                if path.is_file():
                    mlflow.log_artifact(str(path), artifact_path="dataset")
            provenance = root / "training_sources.json"
            if provenance.is_file():
                episodes = json.loads(provenance.read_text())["episodes"]
                mlflow.log_dict(dict(Counter(ep["task"] for ep in episodes)),
                                "dataset/task_counts.json")
            mlflow.set_tags({"logger": "multivla_mlflow_adapter", "robot": "so101e_follower",
                             "action_dim": "7", "camera": "front"})

        def log_dict(self, values, step, mode="train"):
            metrics = {}
            for key, value in values.items():
                if isinstance(value, bool):
                    continue
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    continue
                if math.isfinite(number):
                    metrics[f"{mode}/{key}"] = number
            if metrics:
                mlflow.log_metrics(metrics, step=int(step))

        def log_policy(self, checkpoint_dir):
            path = Path(checkpoint_dir).resolve()
            mlflow.log_dict({"checkpoint_dir": str(path), "weights_uploaded": False},
                            f"checkpoints/{path.name}.json")
            for name in ("config.json", "train_config.json"):
                config = path / "pretrained_model" / name
                if config.is_file():
                    mlflow.log_artifact(str(config), artifact_path=f"checkpoints/{path.name}")

        def log_video(self, path, step, mode="eval"):
            mlflow.log_artifact(str(path), artifact_path=f"{mode}/videos/{step}")

    return MLflowLogger


def execute(mlflow, trainer, args, train_args):
    if mlflow.active_run() is not None:
        raise RuntimeError("Refusing to attach to an unrelated active MLflow run")
    directory = args.mlflow_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = directory / "artifacts"
    artifacts.mkdir(exist_ok=True)
    mlflow.set_tracking_uri("sqlite:///" + str(directory / "mlflow.db"))
    experiment = mlflow.get_experiment_by_name(args.experiment)
    experiment_id = (experiment.experiment_id if experiment else mlflow.create_experiment(
        args.experiment, artifact_location=artifacts.as_uri()))
    run = mlflow.start_run(experiment_id=experiment_id, run_name=args.run_name)
    print(f"MLflow run: {run.info.run_id} | storage: {directory}", flush=True)
    original_logger = trainer.WandBLogger
    original_argv = sys.argv
    status = "FAILED"
    try:
        mlflow.log_dict({p: importlib.metadata.version(p) for p in (
            "lerobot", "torch", "torchvision", "torchcodec", "transformers", "mlflow")},
            "config/package_versions.json")
        trainer.WandBLogger = logger_class(mlflow)
        # The official logging branch is enabled; our replacement never calls wandb.
        sys.argv = [original_argv[0], *train_args, "--wandb.enable=true",
                    "--wandb.project=multivla_mlflow_adapter"]
        trainer.main()
        status = "FINISHED"
    except KeyboardInterrupt:
        status = "KILLED"
        raise
    finally:
        trainer.WandBLogger = original_logger
        sys.argv = original_argv
        mlflow.end_run(status=status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlflow-dir", type=Path, required=True)
    parser.add_argument("--experiment", default="multivla-so101e")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("training_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if importlib.metadata.version("lerobot") != "0.6.1":
        parser.error("MLflow adapter is verified against LeRobot 0.6.1; inspect API before changing version")
    import mlflow
    from lerobot.scripts import lerobot_train
    train_args = args.training_args
    if train_args[:1] == ["--"]:
        train_args = train_args[1:]
    execute(mlflow, lerobot_train, args, train_args)


if __name__ == "__main__":
    main()
