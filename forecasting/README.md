Forecasting uses recorded, non-voided sales and the `daily-sarima-v1` engine.
The API serves saved evaluations; it does not fit models during requests.

Refresh the production evaluation with:

```bash
venv/bin/python manage.py evaluate_forecast
```

The segmented, tuned, customer-grouped, and simulated-sales experiments have
been retired. Migration `0003_remove_experiment_storage` drops the simulation
tables and removes only the three retired experiment versions from real-scope
`ForecastRun` records. Production snapshots and issued forecasts are retained.
Back up the database before applying this migration: reversing it recreates
the simulation schema but cannot recover deleted results.

The original experiment source is preserved by the
`backend-beta-2026-10-15` Git tag. Private database backups and archived exports
belong under the ignored `backups/` directory and must not be committed.
