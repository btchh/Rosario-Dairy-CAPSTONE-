# Rosario Dairy backend

Django/PostgreSQL API for inventory, sales, reporting and SARIMA forecasts.

Report previews and PDF exports accept inclusive `start_date` and `end_date`
for every report type. Inventory ledgers reconstruct movements and balances for
the selected dates; their separate health section is explicitly current stock,
not a historical FEFO snapshot. Reconstruction uses retained stock movements and
the current product catalog. Forecast reports filter approved, available
predictions whose periods overlap the range, preserving full-period amounts
without prorating or training on request. A date range does not create missing
or historical forecasts. Defaults remain report-specific when dates are omitted.

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
from Git. Apply `sales.0017_transaction_void_reason_transaction_voided_at_and_more`
along with all other pending migrations before serving requests.

Production requires explicit `DB_NAME`, `DB_USER`, `DB_PASSWORD` and `DB_HOST`;
it rejects the development `postgres`/`root` users and `root` password. Provision
a dedicated runtime role with only the required application schema/table/
sequence access, separate from migration/administration credentials. Validate
the effective role using the **runtime credentials** before deployment:

```bash
venv/bin/python manage.py check --deploy --database default
```

The additional read-only production check rejects superuser, database-creation,
role-creation, replication and RLS-bypass flags. The database administrator must
also review grants and role memberships; this check does not prove least
privilege by itself. SQL backup restore verification creates/drops an isolated
database and therefore needs separate operator credentials with `CREATEDB`;
do not grant this privilege to the web application just to run backups.

Production rejects disabled HTTPS redirects/secure cookies, nonpositive HSTS,
wildcard allowed hosts, and configured browser origins without explicit HTTPS.
Proxy header trust is off unless `DJANGO_BEHIND_HTTPS_PROXY=true` is explicitly
set. Enable it only behind a trusted proxy that overwrites forwarding headers
and blocks direct application access. Configure the real frontend/API hosts,
Redis and SMTP through the deployment environment; local development remains
unchanged. A passing configuration check does not verify the hosting setup.

SQL exports and restore verification explicitly use UTF-8, independent of the
operating system's text encoding. Backup permission tests verify directory mode
0700 and file mode 0600 on POSIX. On Windows, `chmod` does not establish equivalent
ACL isolation. Both backup commands require Python 3.12.4+ and an ACL-capable
Windows filesystem (such as NTFS), checked before output is created. They use
`mkdir(mode=0o700)` to create a new protected directory whose ACL grants the
owner, SYSTEM and administrators inheritable access. Existing output directories
are rejected. Unsupported runtimes, unsupported volumes and failed volume checks
stop the backup before sensitive files are written. POSIX protection is unchanged.
Run `python manage.py test sales.test_backup_security sales.test_dummy_cleanup
sales.test_history_sql_backup` on the intended Windows host to verify native
directory/file ACLs and isolated restore behavior. Use a development database role
permitted to create disposable test databases; never point restore tests at live
data. Native Windows ACL protection has not been verified by this Linux test run.
See the [Python directory permission documentation](https://docs.python.org/3.12/library/os.html#os.mkdir),
[Windows persistent ACL documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationw)
and [Django deployment checklist](https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/).

## Retraining

The web process reads saved forecasts. A separate scheduler must run maintenance
at 00:00 Asia/Manila; starting the web server does not start the scheduler.
Use either a supervised `venv/bin/python manage.py watch_forecast_models` process
or a platform scheduler running `venv/bin/python manage.py run_forecast_refresh`.
Linux timer examples are in `forecasting/deploy/`; adapt the service account,
paths and environment file to the selected host before installing them.
The supervised worker refreshes immediately on startup to catch up after
downtime, then waits until midnight. The supplied persistent timer catches up a
missed run when reactivated. Both use the same maintenance lock, so a concurrent
manual refresh reuses active work. Catch-up refreshes today's graph; it does not
backdate forecasts for missed days.
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

The primary regular monthly model is seasonal SARIMA. Eligible regular weekly
graphs and provisional projections now reuse the same saved annual SARIMA fit,
with its configuration chosen on earlier validation. This is the user's retained
model-family choice; the weekly baseline comparison remains disclosed. Sparse feeding history can
produce flat estimates. Below 15% regular monthly WAPE remains a target, not a
promise or a display blocker. Original forecasts are distinguished from
reconstructed historical tests; accuracy must be measured against later actuals.

Run `venv/bin/python manage.py audit_forecast_accuracy` for a read-only audit of
current-source saved results. It recomputes monthly WAPE, MAE, RMSE, signed bias,
error dispersion and baseline skill on matching test periods, and reports stale
training and coverage gaps. It never fits or issues forecasts. Currency rounding
is not forecast precision; error dispersion is not a calibrated probability
interval. One-month rolling scores do not establish longer-horizon accuracy.

Provisional selection freezes the earlier-validation winner. If its test fit or
final refit fails, the estimate is unavailable rather than switching to another
candidate after inspecting the test. The corrected provisional v4 and centered
graph v6 engines require `venv/bin/python manage.py run_forecast_refresh` after
updating; older saved model records and issued predictions remain intact.
Current/future graph points share one latest closed-period origin; older issues
are separate comparisons. The current annual outlook combines completed-month
actuals with the remaining monthly estimates and discloses any current-month
actual floor. See `FORECAST_PATTERN_REVIEW.md` for source-pattern evidence,
evaluation limits and the frontend contract additions.

Graph v6 also returns `seasonal_outlook`: 52 weekly or 12 monthly saved points
starting with the current bucket. The existing centered windows remain intact;
yearly has no synthetic annual-cycle expansion. Model details expose actual
projection steps from fitted history, source freshness and correctly scoped
rolling backtests. Saved availability does not imply fresh data or production
approval. See `FORECAST_FINALIZATION.md` and `FRONTEND_HANDOFF.md` for behavior,
verification and the remaining frontend adoption.

The original-only Chapter 4 comparison covers ARIMA, SARIMA, calendar SARIMAX,
smoothing and four ML methods. Its frozen runner, formulas embedded in the HTML,
fitted results, figures, CSVs and Excel/PDF reports remain private local artifacts
in `forecast_experiments/chapter4-comparison-20261028/`; they are not included in
repository pushes. The comparison itself did not change served models, APIs or
application dependencies. The subsequent retained-SARIMA implementation is
documented separately in `FORECAST_FINALIZATION.md`.

The dummy-sales generator has been removed. Preview cleanup of previously
generated data with `venv/bin/python manage.py remove_dummy_data`. Apply it with
`--apply --output backups/dummy-cleanup-UNIQUE-DATE`, using a new private directory.
The command backs up the full database, removes only reserved `2026:NNNNNN`
receipts and their orders/items plus explicit demo graphs and synthetic
experiments, and verifies that every unrelated record remains unchanged. Live
stock references or stock-return records require reconciliation before removal.
Shared customers, products, stock and real forecast history are retained. Existing
SQL backups and original source files remain private and are not removed.
Demo comparisons are unavailable in production.
Development-only comparisons require explicit `scope=demo2026` and an offline
refresh using `refresh_beta_graphs --scope demo2026`. Legacy reserved demo
references are excluded from training. Ordinary local records are included;
this filter does not independently verify their provenance. The authorized
2026 demonstration extension has now been removed using its exact private
manifest; see `DATA_CLEANUP_REPORT.md`. Use a reconciled,
real-only dataset for production; other sales/report APIs are not a mechanism
for filtering an imported demonstration database.

## Inventory consistency

Batch creation, edits and deletion, and ingredient catalog edits require an
admin. Staff can still read visible inventory, record stock adjustments/counts,
and explicitly deactivate ingredients. Received batches cannot be deleted;
record an adjustment to retain their stock history. Past-date available batches
and explicitly expired batches support `expired`/`spoilage` write-offs. Repeating
a completed disposal returns 400 without recording another movement. Future
receipt dates are rejected; legacy future receipts are excluded from sellable
stock and the physical ledger until their receipt date.

Batch metadata edits lock/reload the current batch before saving. Staff caches
verify category visibility and product assignments directly against the database.
Cancellations record actual restored units and dates; the ledger retains physical
deductions and credits returns through signed adjustments and remarks. Existing
report fields are unchanged.

## Sale retries and cancellation

`POST /sales/checkout/` and `/sales/orders/` accept an optional UUID `request_id`
in the JSON body or UUID `Idempotency-Key` header. If both are supplied they must
match. Reusing the same ID for the same account and payload returns the original
sale with 200; the first successful creation returns 201. Changed payloads or
switching between those two endpoints with the same ID returns 409. Failed
submissions roll back the key and stock changes together. Retry protection is
durable across restarts and serializes simultaneous submissions before FEFO.
IDs remain optional for compatibility: unkeyed requests cannot be safely deduplicated.
Manual encoding retains its existing required UUID and replay contract.

Admins can cancel any local sale using
`POST /sales/transactions/{id}/cancel/` with `{}` or an optional `reason` string
(maximum 2000 characters). Encoded sales also expose
`POST /sales/encoding/{id}/cancel/`. Existing order cancellation uses the same
locked transaction workflow. Repeating cancellation returns 400 without another
stock return. Imported history remains protected. Responses expose `voided_at`,
`voided_by` and `void_reason`; earlier cancellations retain unknown actor/time.

Transaction responses add nullable `recorded_cashier`. Imported and manually
encoded invoices return null because their original cashier is unknown;
`handled_by` still preserves the importer/encoder. Live checkout/order sales
return their recorded handler. Existing response fields remain compatible.

Deactivation revokes existing access and refresh tokens permanently, including
after reactivation. Reactivated accounts must log in again.

## Business calendar and live graph values

`DJANGO_TIME_ZONE` is the shared business timezone for HTTP requests, management
commands and the supervised worker. The settings API reports this value and
rejects a conflicting timezone update. Legacy database timezone preferences do
not override it. The supplied systemd timer uses Asia/Manila; update its
`OnCalendar` if deploying with another business timezone.

Today's sales update graph actuals/evaluation without clearing unchanged saved
predictions or training during GET. Corrections that change fitting inputs still
require an offline refresh. Run `venv/bin/python manage.py run_forecast_refresh`
once after this update to prepare runs using the revised graph identity; older
issued forecasts and fitted artifacts are retained.

Runtime-settings cache invalidation runs after commit. Uncommitted settings are
never written to the shared runtime cache.

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
