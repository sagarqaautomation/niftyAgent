"""v2.1 quality settings kept separate from broker credentials and legacy config."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class QualitySettings:
    minimum_history_bars: int = _int("MINIMUM_HISTORY_BARS", 60)
    min_trend_adx: float = _float("MIN_TREND_ADX", 20.0)
    strong_trend_adx: float = _float("STRONG_TREND_ADX", 25.0)
    range_market_min_score: int = _int("RANGE_MARKET_MIN_SCORE", 8)
    high_volatility_min_score: int = _int("HIGH_VOLATILITY_MIN_SCORE", 8)
    high_volatility_atr_pct: float = _float("HIGH_VOLATILITY_ATR_PCT", 0.30)
    relative_volume_threshold: float = _float("RELATIVE_VOLUME_THRESHOLD", 1.25)
    minimum_candle_strength: float = _float("MINIMUM_CANDLE_STRENGTH", 0.65)
    minimum_reward_risk: float = _float("MINIMUM_REWARD_RISK", 1.5)
    signal_cooldown_minutes: int = _int("SIGNAL_COOLDOWN_MINUTES", 5)
    setup_reset_bars: int = _int("SETUP_RESET_BARS", 3)
    news_max_age_minutes: int = _int("NEWS_MAX_AGE_MINUTES", 60)

    # Experimental strict filter. Keep disabled by default until it proves
    # an out-of-sample improvement in walk-forward validation.
    precision_mode: bool = _bool("PRECISION_MODE", False)
    precision_min_score: int = _int("PRECISION_MIN_SCORE", 8)
    precision_min_adx: float = _float("PRECISION_MIN_ADX", 25.0)
    precision_require_structure: bool = _bool("PRECISION_REQUIRE_STRUCTURE", True)
    precision_require_candle: bool = _bool("PRECISION_REQUIRE_CANDLE", True)
    precision_require_rsi: bool = _bool("PRECISION_REQUIRE_RSI", True)


quality_settings = QualitySettings()
