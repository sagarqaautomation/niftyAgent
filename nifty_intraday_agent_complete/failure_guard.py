"""Guards derived from analysed failed NIFTY CALL/PUT signals.

Each rule maps to a recurring loss cluster found in resolved signals:
  * opening noise: signals fired before the opening range settled
  * trend exhaustion: 5m ADX far above the trending band (late entries)
  * RSI extremes: chasing an already overbought/oversold move
  * loss streak: repeating the same direction right after a failed signal
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from accuracy_config import QualitySettings, quality_settings


def _num(section: Mapping[str, Any], key: str) -> float | None:
    value = section.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def failure_guard_reason(
    direction: str,
    signal_time: datetime,
    snapshot: Mapping[str, Any],
    recent_signals: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
    settings: QualitySettings = quality_settings,
) -> str | None:
    """Return a block reason when the setup matches a known failure pattern."""
    if not settings.failure_guard_enabled or direction not in {"CALL", "PUT"}:
        return None

    minutes = signal_time.hour * 60 + signal_time.minute
    if minutes < settings.guard_earliest_signal_minute:
        return (
            "Opening-noise guard: earlier signals fired before "
            f"{settings.guard_earliest_signal_minute // 60:02d}:"
            f"{settings.guard_earliest_signal_minute % 60:02d} IST failed repeatedly."
        )

    one = snapshot.get("one_minute") or {}
    five = snapshot.get("five_minute") or {}

    adx5 = _num(five, "adx14")
    if adx5 is not None and adx5 > settings.guard_max_five_min_adx:
        return (
            f"Trend-exhaustion guard: 5m ADX {adx5:.1f} > "
            f"{settings.guard_max_five_min_adx:.0f}; late entries reversed."
        )

    rsi1, rsi5 = _num(one, "rsi14"), _num(five, "rsi14")
    if direction == "CALL" and rsi1 is not None and rsi1 >= settings.guard_call_max_rsi:
        return f"Overbought guard: 1m RSI {rsi1:.1f} - CALL would chase an extended move."
    if direction == "PUT" and (
        (rsi1 is not None and rsi1 <= settings.guard_put_min_rsi)
        or (rsi5 is not None and rsi5 <= settings.guard_put_min_five_min_rsi)
    ):
        return "Oversold guard: RSI already extreme - PUT would chase an extended move."

    now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=settings.guard_loss_streak_minutes)
    for previous in recent_signals:
        if previous.get("signal") != direction or previous.get("status") != "FAILED":
            continue
        try:
            created = datetime.fromisoformat(str(previous["created_at"]))
        except (KeyError, ValueError):
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created <= window:
            return (
                f"Loss-streak guard: a {direction} failed within the last "
                f"{settings.guard_loss_streak_minutes} minutes."
            )
    return None
