"""Exercise logger hooks and run lifecycle without GPU or the LeRobot package."""
import argparse
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import train_with_mlflow as tracking


class MLflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = MagicMock()
        self.api.active_run.return_value = None
        self.api.get_experiment_by_name.return_value = None
        self.api.create_experiment.return_value = "1"
        self.args = argparse.Namespace(mlflow_dir=self.root / "tracking",
                                      experiment="test", run_name="smoke")

    def logger(self):
        cfg = SimpleNamespace(output_dir=self.root / "run",
            dataset=SimpleNamespace(root=self.root),
            to_dict=lambda: {"batch_size": 8, "policy": {"train_state_proj": True}})
        self.api.active_run.return_value = object()
        return tracking.logger_class(self.api)(cfg)

    def test_logs_resolved_parameters(self):
        self.logger()
        self.api.log_params.assert_called_with({"batch_size": "8", "policy.train_state_proj": "True"})

    def test_metrics_have_step_and_train_eval_namespaces(self):
        logger = self.logger()
        logger.log_dict({"loss": 1.25, "lr": 1e-4, "bad": float("nan"), "text": "x"}, 10)
        self.api.log_metrics.assert_called_with({"train/loss": 1.25, "train/lr": 1e-4}, step=10)
        logger.log_dict({"eval_loss": .5}, 20, mode="eval")
        self.api.log_metrics.assert_called_with({"eval/eval_loss": .5}, step=20)

    def test_checkpoints_log_reference_not_weights(self):
        logger = self.logger()
        logger.log_policy(self.root / "checkpoints/000020")
        payload = self.api.log_dict.call_args.args[0]
        self.assertFalse(payload["weights_uploaded"])
        self.api.log_artifacts.assert_not_called()

    def test_lifecycle_and_original_logger_restored(self):
        for exception, status in ((None, "FINISHED"), (RuntimeError("failed"), "FAILED"),
                                  (KeyboardInterrupt(), "KILLED")):
            with self.subTest(status=status):
                self.api.active_run.return_value = None
                old_logger = object()
                trainer = SimpleNamespace(WandBLogger=old_logger, main=MagicMock(side_effect=exception))
                old_argv = sys.argv
                with patch.object(tracking.importlib.metadata, "version", return_value="test"):
                    if exception:
                        with self.assertRaises(type(exception)):
                            tracking.execute(self.api, trainer, self.args, ["--steps=20"])
                    else:
                        tracking.execute(self.api, trainer, self.args, ["--steps=20"])
                self.api.end_run.assert_called_with(status=status)
                self.assertIs(trainer.WandBLogger, old_logger)
                self.assertIs(sys.argv, old_argv)

    def test_sqlite_uri_and_explicit_artifact_location(self):
        trainer = SimpleNamespace(WandBLogger=object(), main=lambda: None)
        with patch.object(tracking.importlib.metadata, "version", return_value="test"):
            tracking.execute(self.api, trainer, self.args, [])
        self.api.set_tracking_uri.assert_called_with("sqlite:///" + str(self.args.mlflow_dir.resolve() / "mlflow.db"))
        self.api.create_experiment.assert_called_with("test", artifact_location=(
            self.args.mlflow_dir.resolve() / "artifacts").as_uri())


if __name__ == "__main__":
    unittest.main()
