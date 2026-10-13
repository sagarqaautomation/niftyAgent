# Pre-market intelligence (research feature)

This feature adds a provider-agnostic pre-market context scorer for the
`v2.1-accuracy-engine` line. It does **not** place orders or claim to fetch live
market data. A data adapter must supply observations from a licensed provider.

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

## Integration and validation plan

1. Implement separate provider adapters for GIFT Nifty, VIX, global indices,
   breadth and timestamped news. Store source, observed_at, fetched_at and
   missing/error status for every observation.
2. Persist the pre-market snapshot before the open and immutable intraday
   snapshots thereafter. Do not overwrite historical observations.
3. Add these values as candidate ML features alongside existing technical
   features; keep the current live CALL/PUT/WAIT engine unchanged initially.
4. Compare baseline vs pre-market features vs full-context features using
   chronological walk-forward validation. Report trade count, win rate,
   expectancy after costs, drawdown, and CALL/PUT breakdown.
5. Test stability by month and volatility regime; do not tune on the final
   holdout. Only promote features that improve out-of-sample results.

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
