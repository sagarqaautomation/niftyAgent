import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ml_experiment import load_dataset, run_experiment


class MLExperimentTests(unittest.TestCase):
    def _frame(self, n: int = 120) -> pd.DataFrame:
        rng = np.random.default_rng(17)
        x = rng.normal(size=n)
        y = (x + rng.normal(scale=0.8, size=n) > 0).astype(int)
        return pd.DataFrame({
            "timestamp": pd.date_range("2025-01-01", periods=n, freq="min", tz="UTC"),
            "target": y,
            "momentum": x,
            "volatility": rng.uniform(0.1, 2.0, size=n),
        })

    def test_loader_sorts_and_rejects_non_binary_target(self) -> None:
        frame = self._frame()
        frame = pd.concat([frame.iloc[::-1]], ignore_index=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.csv"
            frame.to_csv(path, index=False)
            loaded = load_dataset(path, ["momentum", "volatility"])
            self.assertTrue(loaded["timestamp"].is_monotonic_increasing)
            self.assertEqual(len(loaded), len(frame))

            frame.loc[0, "target"] = 2
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "binary labels"):
                load_dataset(path, ["momentum", "volatility"])

    def test_requires_explicit_features_and_excludes_target(self) -> None:
        frame = self._frame()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.csv"
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "at least one numeric feature"):
                load_dataset(path, [])
            with self.assertRaisesRegex(ValueError, "cannot be model features"):
                load_dataset(path, ["target", "momentum"])

    def test_walk_forward_and_final_holdout_report_all_models(self) -> None:
        frame = self._frame()
        report = run_experiment(frame, ["momentum", "volatility"], folds=3)
        self.assertEqual(report["rows"], 120)
        self.assertEqual(set(report["final_holdout"]), {
            "majority_baseline", "logistic_regression", "rbf_svm"
        })
        for model_report in report["final_holdout"].values():
            self.assertIn("balanced_accuracy", model_report)
            self.assertIn("precision", model_report)
            self.assertIn("recall", model_report)
            self.assertIn("f1", model_report)
            self.assertEqual(model_report["samples"], 24)
        self.assertTrue(all(count >= 1 for count in report["folds_evaluated"].values()))

    def test_rejects_missing_both_class_holdout(self) -> None:
        frame = self._frame()
        frame.loc[96:, "target"] = 1
        with self.assertRaisesRegex(ValueError, "Both classes must occur"):
            run_experiment(frame, ["momentum", "volatility"], folds=3)


if __name__ == "__main__":
    unittest.main()
