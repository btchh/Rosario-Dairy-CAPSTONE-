The 2026 scenario is stored in `forecasting_simulationrun` and
`forecasting_simulatedsalesday`, separate from sales transactions. Each run has
365 daily revenue rows, split into feeding and other sales, plus a historical
source hash, random seed, method version, and the sampled donor weeks.

Generate or retrieve the same scenario and export it privately:

```bash
venv/bin/python manage.py generate_sales_simulation --seed 2026 --output forecast_experiments/simulation_2026
```

The generator samples 2023–2025 weeks jointly across sales segments, aligning
calendar month and weekday. Donor years have weights 20%, 30%, and 50%.
It normalizes donor revenue by that year's daily mean and scales to the 2025
daily mean with the median annual growth, bounded to ±25%. This is a modeling
assumption, not a fitted or validated forecast. Zeros and large sales remain in
the donor samples. Movable holidays, new contracts, and future business changes
are not modeled. No values are tuned to a WAPE target.

Re-running with the same source, method, and seed reuses the database run.
Another seed creates a separate scenario. CSV exports explicitly identify the
data as synthetic. Use a separate output directory for each scenario.

For an experimental training series, read `SimulationRun.objects.get(pk=...).days`
and sum `feeding_revenue` and `other_sales_revenue` by date. Keep its run ID
attached to any experimental model. The existing real-sales source and evaluator
do not consume simulation rows, so they cannot silently count simulated values
as actual observations.

The existing WAPE evaluator selects using pre-2025 periods and scores real 2025
sales. Its saved errors remain the evidence for that retrospective test. Training
on a generated 2026 path cannot establish a sub-30% real-world WAPE. An experiment
trained through simulated 2026 must be evaluated separately from real-data
backtests and must not use real 2025 as a chronological holdout after training on
2026. Future accuracy requires later observed sales.
