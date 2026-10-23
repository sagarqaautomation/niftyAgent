# NIFTY ML experiment: chronological baseline vs SVM

This experiment is intentionally separate from the live signal engine. It does **not** change CALL/PUT/WAIT decisions and does not place orders.

## Dataset contract

Provide a CSV with:
- `timestamp`: timestamp at which the prediction would have been made.
- `target`: binary 0/1 label. Prefer a resolved historical strategy outcome (for example, target hit before stop within the configured expiry), not a future close copied into a feature.
- Numeric feature columns available **at that timestamp**.

Feature selection is an explicit allowlist. Do not include IDs, future prices, exit fields, trade status, or columns computed using future candles. Use only data that would have been known when the signal was generated. Sorts are chronological; duplicate timestamps are reduced to the last row.

## Run it

Install the project dependencies in your active environment:

```bash
pip install -r requirements.txt
```

Then run, replacing the CSV path and feature names with your actual exported dataset:

```bash
python ml_experiment.py data/nifty_training.csv \
  --timestamp-col timestamp \
  --target-col target \
  --features ema9_slope,ema21_slope,rsi14,adx14,atr_pct,vwap_distance_pct,relative_volume20 \
  --folds 4 \
  --holdout-fraction 0.20 \
  --gap 5 \
  --output reports/ml_benchmark.json
```

The example feature names are illustrative. The CSV must actually contain them. The `--gap` should be at least as large as any overlapping label horizon in rows, and may need to be larger if observations are not equally spaced.

## What is evaluated

- Majority-class baseline.
- Logistic regression with class balancing.
- RBF-kernel SVM with class balancing.
- Expanding chronological validation folds on the development period.
- One final, later chronological holdout that is not used for model selection.
- Accuracy, balanced accuracy, precision, recall, F1 and ROC AUC where defined.
- Fold and holdout date ranges, so temporal order is auditable.

Imputation and scaling are part of each fitted pipeline, so statistics are learned only from the training partition. This is important for SVMs because feature scaling can materially affect their behavior; see the [scikit-learn StandardScaler documentation](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.StandardScaler.html) and [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Important limitations

1. This is a **classification benchmark**, not a profitability backtest. Better F1 or ROC AUC does not prove that CALL/PUT trades make money.
2. Do not train on unexpired trades or count ambiguous/expired trades as losses/wins by assumption. Define labels consistently from the strategy's resolved outcomes.
3. A single row per decision time is expected. If the strategy creates multiple candidate trades at the same timestamp, include a unique candidate key and adapt deduplication before using that dataset.
4. The final holdout must not be repeatedly used to tune parameters. After choosing a candidate using walk-forward folds, evaluate once on the holdout.
5. Compare model results with actual strategy outcomes after brokerage, taxes, spread and slippage. For options, use option-level entry/exit prices and contract costs rather than assuming NIFTY spot points equal option P&L.
6. The model is not connected to the live signal engine and does not alter its predictions. Integration should happen only after reproducible out-of-sample trading improvements are demonstrated.
7. The repository does not currently provide a confirmed training CSV in this branch, so real performance numbers cannot be reported until a properly labeled dataset is supplied.

## Output

The JSON report is written to the path given by `--output`. Inspect `walk_forward` and `final_holdout` for every model. Treat the majority baseline as a minimum benchmark, not a deployable trading strategy.
