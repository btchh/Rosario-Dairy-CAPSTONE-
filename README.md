# Rosario Dairy backend

Django/PostgreSQL API for inventory, sales, reporting and SARIMA forecasts.

## Start

Always apply migrations before starting, including after pulling backend code:

```bash
venv/bin/python -m pip install -r requirements.txt
venv/bin/python manage.py migrate
```

For local development, run `venv/bin/python manage.py runserver 127.0.0.1:8000`.
For production, configure the secrets/settings in `deployment.env.example`, run
`venv/bin/python manage.py collectstatic --noinput`, and serve
`config.wsgi:application` with Gunicorn behind the hosting provider's HTTPS proxy.
Keep SQL recovery backups and original source files private; they are excluded
from Git. Apply `sales.0016_record_cancellation_stock_returns` along with all other
pending migrations before serving requests.

## Retraining

The web process reads saved forecasts. A separate scheduler must run maintenance
at 00:00 Asia/Manila; starting the web server does not start the scheduler.
Use either a supervised `venv/bin/python manage.py watch_forecast_models` process
or a platform scheduler running `venv/bin/python manage.py run_forecast_refresh`.
Linux timer examples are in `forecasting/deploy/`; adapt the service account,
paths and environment file to the selected host before installing them.
Manual refresh uses the same maintenance lock and saved job status as scheduled
refresh. The persistent worker/hosting setup still needs to be provisioned.

Normal maintenance refreshes real-sales graphs only. GET requests never train.
Changed eligible real sales or corrections can retrain; unchanged inputs reuse
saved fitted parameters. Existing issued predictions and earlier model records
are retained for later comparisons. Database migrations do not guess missing
historical cancellation dates or return quantities.

## Forecasting

`/api/forecasting/graph/` provides Combined, Regular sales and Milk feeding, with
three series: past predictions, recorded sales and future predictions. Weekly
and monthly ranges contain three earlier periods, the selected period and three
later periods. Yearly contains two earlier years, the selected year and two later
years. See `FRONTEND_HANDOFF.md` for the API contract. Existing chart endpoints
remain compatible until the frontend adopts the new graph API.

Only eligible real dairy invoices train the models. Explicit imported sales
classifications take precedence; otherwise feeding detection uses the recorded
customer label/name containing `feedin`, ignoring case. Unclassified entries stay
in ledger/reports but do not train either segment. This naming rule is retained;
an explicit transaction classification API is a future task if labels change.

The primary regular monthly model is seasonal SARIMA. Sparse feeding history can
produce flat estimates. Below 15% regular monthly WAPE remains a target, not a
promise or a display blocker. Original forecasts are distinguished from
reconstructed historical tests; accuracy must be measured against later actuals.

Demo comparisons and dummy-sales generation are unavailable in production.
Development-only comparisons require explicit `scope=demo2026` and an offline
refresh using `refresh_beta_graphs --scope demo2026`. Generated sales never train
the models. No dummy receipts, SQL backups or archived forecasts are deleted by
this cleanup. Use a real-only dataset for production; other sales/report APIs
are not a mechanism for filtering an imported demonstration database.

## Inventory consistency

Batch metadata edits lock/reload the current batch before saving. Staff caches
verify category visibility and product assignments directly against the database.
Cancellations record actual restored units and dates; the ledger retains physical
deductions and credits returns through signed adjustments and remarks. Existing
report fields are unchanged.

## Verify

Run against a development environment with its own test database and cache:

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py test --keepdb --noinput
```

Regression tests, migrations and operational evidence commands are retained.
No additional dependencies are needed for these changes.

Known maintenance limitation: if shared-cache invalidation fails after a commit,
ordinary cached business values may stay stale until expiry or a later successful
invalidation. Staff visibility is independently checked before cache lookup, and
the new graph API checks its source revision directly. Production needs a
least-privilege database role rather than the development database privileges.
