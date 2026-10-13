import json
import unittest

from probability_engine import fit_probability_model, predict


class ProbabilityEngineTests(unittest.TestCase):
    def test_logistic_model_learns_joint_setup_pattern(self) -> None:
        trades = []
        for _ in range(30):
            for direction, regime in (
                ("CALL", "TREND"),
                ("CALL", "RANGE"),
                ("PUT", "TREND"),
                ("PUT", "RANGE"),
            ):
                wins = (direction == "CALL" and regime == "TREND") or (
                    direction == "PUT" and regime == "RANGE"
                )
                trades.append({
                    "direction": direction,
                    "regime": regime,
                    "status": "SUCCESS" if wins else "FAILED",
                    "hour": 10,
                    "adx": 23,
                    "rsi": 50,
                    "relative_volume": 1.1,
                    "vwap_distance_pct": 0.0,
                    "candle_strength": 0.6,
                    "structure": "NONE",
                })

        model = json.loads(json.dumps(fit_probability_model(trades)))
        good_probability, evidence = predict(model, {"direction": "CALL", "regime": "TREND"})
        bad_probability, _ = predict(model, {"direction": "CALL", "regime": "RANGE"})

        self.assertEqual(model["method"], "regularized_logistic_regression")
        self.assertGreater(evidence, 40)
        self.assertGreater(good_probability or 0, 0.6)
        self.assertLess(bad_probability or 1, 0.4)

    def test_individual_candlestick_feature_changes_probability(self) -> None:
        trades = []
        for _ in range(30):
            trades.append({
                "direction": "CALL", "regime": "TREND", "status": "SUCCESS",
                "hour": 10, "adx": 23, "rsi": 60, "relative_volume": 1.1,
                "vwap_distance_pct": 0.2, "candle_strength": 0.6,
                "structure": "NONE", "pattern_hammer": True,
            })
            trades.append({
                "direction": "CALL", "regime": "TREND", "status": "FAILED",
                "hour": 10, "adx": 23, "rsi": 60, "relative_volume": 1.1,
                "vwap_distance_pct": 0.2, "candle_strength": 0.6,
                "structure": "NONE", "pattern_hammer": False,
            })

        model = fit_probability_model(trades)
        with_hammer, _ = predict(model, {
            "direction": "CALL", "regime": "TREND", "hour": 10, "adx": 23,
            "rsi": 60, "relative_volume": 1.1, "vwap_distance_pct": 0.2,
            "candle_strength": 0.6, "structure": "NONE", "pattern_hammer": True,
        })
        without_hammer, _ = predict(model, {
            "direction": "CALL", "regime": "TREND", "hour": 10, "adx": 23,
            "rsi": 60, "relative_volume": 1.1, "vwap_distance_pct": 0.2,
            "candle_strength": 0.6, "structure": "NONE", "pattern_hammer": False,
        })

        self.assertGreater(with_hammer or 0, without_hammer or 1)

    def test_predict_remains_compatible_with_saved_v2_model(self) -> None:
        model = {
            "version": 2,
            "prior": 0.5,
            "features": {"direction": {"CALL": {"samples": 50, "contribution": 1.0}}},
        }

        probability, evidence = predict(model, {"direction": "CALL"})

        self.assertGreater(probability or 0, 0.5)
        self.assertEqual(evidence, 50)


if __name__ == "__main__":
    unittest.main()