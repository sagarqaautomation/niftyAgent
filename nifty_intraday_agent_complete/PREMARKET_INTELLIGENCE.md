# Pre-market intelligence (research feature)

This feature adds a pre-market scorer and best-effort live collector to the
`v2.1-accuracy-engine` line. It does **not** place orders. The collector uses
Kite quotes for India VIX and an optional configured GIFT Nifty quote key, Yahoo
Finance daily chart data for global index context, the configured NIFTY 50
weights file for breadth, and timestamped items from the existing news pipeline.
Provider availability and licensing can vary; unavailable fields are left missing.

## Inputs to collect (with observation timestamps)

- **GIFT Nifty:** latest futures percentage change and ideally multiple samples
  to identify whether the move is strengthening or fading. This replaces the
  outdated SGX Nifty reference.
- **Global markets:** overnight US/Asian index moves and major scheduled/event
  risk, normalized to a sentiment value in [-1, 1].
- **India VIX:** value and change. Use it for volatility/risk regime only, not
  as a bullish/bearish directional feature by itself.
- **Market breadth:** advancing/declining counts or ratio. Before 09:15 IST,
  use only the latest completed session or actual pre-open data available at
  that timestamp. Do not use future/current-session values prematurely.
- **News:** timestamped, deduplicated headlines tied to NIFTY constituents;
  prefer licensed/official sources and index-weighted impacts where reliable.

## Current implementation

`premarket_engine.py:build_premarket_context(...)` is a pure scoring function.
It returns directional bias, component scores, volatility regime and risk flags.
Weights (GIFT Nifty 35%, global sentiment 25%, breadth 25%, news 15%) are
initial experiment defaults, **not validated model coefficients**. Missing
inputs are excluded and the remaining weights renormalized. With fewer than
two directional components, the result is `INSUFFICIENT_DATA`. It never emits
a CALL/PUT order instruction.

## Current integration

- `premarket_collector.py` collects best-effort inputs every `PREMARKET_REFRESH_SECONDS` (default 300 seconds) while live mode runs.
- Snapshots are appended to SQLite `premarket_snapshots`; they are not overwritten.
- `GET /premarket/latest` returns the latest snapshot and `GET /premarket/history?limit=50` returns recent snapshots.
- The latest snapshot is included in each NIFTY signal's feature snapshot, with flat numeric candidate fields for later model datasets. The current CALL/PUT/WAIT logic is not changed by this context score.
- Before 09:15 IST, the collector deliberately excludes current-session breadth because live constituent prices are not yet available. After the open, it uses available quote changes versus previous close.

## Remaining validation

1. Confirm the Kite account exposes `NSE:INDIA VIX`; configure `PREMARKET_GIFT_NIFTY_QUOTE` only if your provider actually exposes a supported GIFT Nifty instrument. GIFT Nifty is otherwise unavailable rather than guessed.
2. Ensure `data/nifty50_weights.csv` is current and its symbols match Kite quote symbols. Missing/invalid weights reduce breadth reliability.
3. Run the unit tests and a live smoke test in the user's configured environment; external quote endpoints and broker credentials are not available in this code-editing session.
4. Export resolved, timestamped signals and compare baseline vs pre-market candidate features with chronological walk-forward validation. Report trade count, hit rate, expectancy after costs, drawdown and CALL/PUT breakdown.
5. Test stability by month and volatility regime; do not tune on the final holdout. Only promote model features that improve out-of-sample results.

## Example

```python
from premarket_engine import build_premarket_context

context = build_premarket_context(
    gift_nifty_change_pct=0.35,
    global_sentiment=0.4,
    advance_decline_ratio=1.3,
    news_sentiment=0.1,
    india_vix=14.2,
    india_vix_change_pct=-2.0,
    observed_at="2026-10-14T08:55:00+05:30",
)
print(context)
```

The example values are illustrative only, not current market readings.
