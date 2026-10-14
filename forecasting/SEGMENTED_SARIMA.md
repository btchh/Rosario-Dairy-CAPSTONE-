Run the SARIMA-only comparison from the backend directory:

```bash
venv/bin/python manage.py compare_segmented_sarima --workers 4
```

It reads real, non-voided sales and uses the existing feeding/other-sales label
classification. It never reads the simulation tables or changes sales records.

Both approaches use the same protocol: 2023–2024 initial training, January–June
2025 rolling validation, and July–December 2025 rolling retrospective testing.
Weeks crossing a validation/test boundary are excluded from selection. Later
test forecasts may use earlier observed test sales, as in ordinary rolling
forecasting. A separate fixed-origin monthly path never updates after June 30.

The `existing` comparison uses the production daily SARIMA grid, retrained and
selected on this protocol. The `segmented` comparison retains every existing
candidate, adds 56- and 182-day training windows, and tests direct monthly/weekly
SARIMA with annual calendar seasons (12 months or 52 weeks). Every candidate has
an active seasonal AR or MA term. Direct calendar models require at least two
complete annual cycles; daily models use seven-day seasonality. Both levels and
log-transformed models are tested, with mean correction on log forecasts.

Each approach independently selects models for total sales, feeding, and other
sales. Validation error decides between a direct total forecast and the sum of
the two component forecasts. Models, strategy, and interval calibration are
frozen before scoring test sales. Failed fits are recorded and cannot win
validation by omitting difficult dates. Test data do not select winners.

Results are stored as a separate `ForecastRun` version `segmented-sarima-v1`.
The production evaluator and dashboard continue using their existing version.
This command does not issue or publish forecasts. It is a comparison experiment,
not an automatic replacement chosen after looking at test performance.

Private exports in `forecast_experiments/segmented_sarima/` include:

- `comparison.csv`: validation/test WAPE for both approaches and all segments.
- `predictions.csv`: dated predictions, actuals, and training cutoffs.
- `comparison.json`: complete selections, failed candidates, metrics, baselines,
  fixed-origin paths, and limitations.
- `checkpoint.json`: completed periods, reusable only with the same source hash,
  configuration fingerprint, and experiment version.

Repeated runs reuse completed database results. Interrupted runs resume completed
periods from the private checkpoint. Use `--output` to choose another directory.

2025 has already been inspected during development; this is retrospective
evidence. Six monthly test points provide limited evidence, and results must not
be compared directly with the old full-year 2025 WAPE. Milk-feeding activity
begins late in the history, so having two calendar cycles is not evidence of two
active milk-feeding cycles. Report errors for both components and the full total;
a sub-30% component error does not establish sub-30% total-sales error.

The initial real-data experiment was saved as run 29. On the shared test dates:

| Test WAPE | Existing grid | Expanded grid |
| --- | ---: | ---: |
| Monthly total | 33.86% | 33.82% |
| Monthly other sales | 35.01% | 21.78% |
| Monthly feeding | 53.81% | 53.81% |
| Weekly total | 59.98% | 63.81% |
| Weekly other sales | 25.79% | 25.79% |
| Weekly feeding | 84.68% | 88.92% |

Both grids selected component sums using validation. The expanded monthly
other-sales model was SARIMA(0,1,1)(0,0,1)[12] in revenue levels. Feeding retained
the existing 365-day daily seasonal model. Although monthly other-sales error
improved, total error improved by only 0.04 percentage points, and expanded
monthly total validation WAPE was worse (57.32% versus 55.29%). Weekly test error
also worsened despite improved validation. These results do not support claiming
sub-30% total-sales accuracy or automatically replacing production.
