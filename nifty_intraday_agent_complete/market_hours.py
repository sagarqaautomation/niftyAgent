from datetime import datetime, time
from zoneinfo import ZoneInfo

from config import settings


def _in_market_timezone(value: datetime) -> datetime:
    market_timezone = ZoneInfo(settings.market_timezone)
    if value.tzinfo is None:
        return value.replace(tzinfo=market_timezone)
    return value.astimezone(market_timezone)


def is_market_open(now: datetime | None = None) -> bool:
    market_now = _in_market_timezone(now or datetime.now(ZoneInfo(settings.market_timezone)))

    return (
        market_now.weekday() < 5
        and time(9, 0) <= market_now.time() < time(15, 30)
    )


def is_current_session_candle(candle_time: datetime, reference_time: datetime) -> bool:
    market_candle_time = _in_market_timezone(candle_time)
    market_reference_time = _in_market_timezone(reference_time)
    return (
        market_candle_time.date() == market_reference_time.date()
        and is_market_open(market_candle_time)
    )