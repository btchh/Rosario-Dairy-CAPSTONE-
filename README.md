# Rosario Dairy backend

Django/PostgreSQL backend for inventory, sales, reporting and SARIMA forecasts.
The previous working tree is saved in the named Git stash created on 2026-10-23.
Frontend files belong to JayMark's separate repository and were not edited.

## Run

Always apply migrations before starting, including after pulling backend code:

```bash
venv/bin/python -m pip install -r requirements.txt
venv/bin/python manage.py migrate
venv/bin/python manage.py runserver 127.0.0.1:8000
```

Use `deployment.env.example` for configuration names, never real secrets in Git.
Keep private SQL backups under `backups/`; preserve original source data, tests,
migrations and recovery files. Production needs its production settings, HTTPS,
Redis and a least-privilege application database role.

## Forecasting beta

The centered graph has three series: past predictions, recorded sales and future
predictions. Its weekly/monthly windows have three earlier buckets, the current
bucket and three later buckets. Yearly has two earlier years, the current year
and two later years. It supports Combined, Regular sales and Milk feeding.

SARIMA estimates are available without the older production approval gate.
Accuracy is measured separately. Regular monthly error below 15% remains a target,
not a promise. The existing regular monthly historical WAPE of 10.64% does not
establish future accuracy or accuracy for feeding/other horizons. Sparse inputs
can yield flat forecasts; missing data is not invented or zero-filled.

Only real, classified dairy sales train models. Demo scope displays/evaluates
simulated 2026 sales against models fitted to real pre-2026 data. Its scores are
separate from real accuracy. Read requests never fit. Fits and issued future
predictions are saved; later graph refreshes compare original forecasts with
subsequently recorded outcomes. Reconstructed past tests retain their own basis.

Manual refresh and the midnight Asia/Manila worker include the new graph stage.
Unchanged inputs reuse saved portable state; new eligible real data changes the
source revision and can retrain. Sales classification keeps the existing source
policy; this change does not introduce a new sale-entry field.

```bash
venv/bin/python manage.py refresh_beta_graphs
# Optional historical/simulated anchor, prepared offline:
venv/bin/python manage.py refresh_beta_graphs --scope demo2026 --as-of 2026-07-15
```

Migration `forecasting.0012_graphforecastrun_graphprediction` adds isolated graph
runs and original forecast records. Existing models, forecasts and audit history
are retained. The new graph API is additive; existing chart routes stay compatible.
See [API guide](FRONTEND_HANDOFF.md) for the contract without UI design instructions.

## Verify

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py test --keepdb --noinput
```

Tests use a separate PostgreSQL test database. No new dependencies were added.
The broad regression run passed 369 sales, forecasting, report and configuration
tests on 2026-10-23; the final focused run passed all 12 centered-graph tests.
Authenticated HTTP checks through the frontend proxy passed for both scopes and
all three periods, including legacy chart compatibility. Model fitting in tests
uses isolated fixtures.

Known maintenance issue retained from the earlier audit: a cache invalidation
failure after a committed write can allow an old cached payload to return after
recovery. The new graph GET reads revisions directly, but this does not repair
the shared legacy cache path. Existing local development DB privileges also need
review before production deployment.
