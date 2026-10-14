Run the real-sales tuning experiment:

```bash
venv/bin/python manage.py tune_sales_sarima --workers 4
```

This requires a completed `compare_segmented_sarima` run on the same real source.
The 2026 dashboard demo has been removed. Simulation records remain isolated
and are never read by either real-sales experiment.

The protocol retains 2023–2024 initial training, January–June 2025 rolling
validation, and July–December 2025 rolling retrospective testing. Boundary
crossing weeks never enter validation. Test values cannot select a model,
component pair, transformation, correction, or error range. Every real sale,
including the large feeding transactions, remains in total WAPE.

The fixed search contains 59 seasonal specifications per series and reporting
frequency. It retains the previous grid and adds 28-/730-day windows,
log-median forecasts, additional seasonal AR/MA orders and differencing, and
calendar windows containing two annual cycles. Daily seasonality is seven days;
direct calendar models use 12 months or 52 weeks. No arbitrary short calendar
season or nonseasonal model is substituted for SARIMA. Flat prefixes return
their constant observed level. Nonconvergent fits cannot win by omitting dates.

Each component contributes its five best validation candidates to joint
selection. Models from both earlier comparison plans are retained even if
outside these shortlists. The final choice compares those component pairs
against the best direct total-sales SARIMA. Selection minimizes pooled
total-sales validation MAE, which ranks models identically to WAPE on the same
dates. This corrects the previous assumption that individually best component
errors necessarily give the best total forecast. Individually selected segment
models remain available as separate diagnostic results.

`target_met` means combined retrospective WAPE strictly below 25%, with at least
five successful test periods. The stretch target is strictly below 20%. These
are experiment targets; existing publication rules are not silently changed.

Private outputs are stored in `forecast_experiments/tuned_sarima/`:

- `summary.csv` and `predictions.csv` contain selected-plan metrics and predictions.
- `results.json` contains all validation candidates and dated predictions,
  model selection, prior comparison results, baselines, and limitations.
- `checkpoint.json` preserves completed reporting periods.
- `candidates/` preserves each completed candidate. Cache identity includes only
  the history through validation, target dates, model, and method version.

Database results use the separate version `tuned-sarima-v1`; prior audits are
preserved. Repeating the command reuses the matching result. Interrupted runs
resume completed work. Source hashes are rechecked before writing the snapshot.

All of 2025 has already been inspected, and earlier results informed this search.
The experiment is exploratory retrospective evidence, not an independent test of
future accuracy. There are only six monthly test points. Better validation or
component scores do not imply that total-sales test WAPE improved. Report the
frozen selected result even if it misses the target or worsens the earlier test.

The completed first tuning run is snapshot 31. On the same July–December test:

| Total-sales WAPE | Earlier original grid | Earlier expanded grid | New tuning |
| --- | ---: | ---: | ---: |
| Monthly (6 periods) | 33.86% | 33.82% | 42.86% |
| Weekly (25 periods) | 59.98% | 63.81% | 60.28% |

The new monthly validation WAPE improved to 46.09%, but its test error worsened.
Weekly validation improved to 41.78%, but test WAPE remains above target. The
expanded search therefore does not establish a sub-25% combined-sales model.

The independently selected monthly other-sales SARIMA improved from 21.78% to
20.85% WAPE. Its model is SARIMA(0,1,1)(0,0,1)[12] using log-median predictions
and the full available monthly prefix. This is a component diagnostic, not the
selected direct total-sales model. The weekly joint plan's other-sales component
scored 22.51%, while feeding scored 87.05%. Improvements to regular sales do not
resolve the unpredictable starts and stops in feeding transactions.

The new model is stored for review; the production evaluator was not replaced.
Real-sales records and their source hash were verified unchanged. Frontend demo
changes and the demo API/builder/snapshot were removed. The frontend again matches
the teammate's main branch. The isolated earlier simulation dataset is not used.
