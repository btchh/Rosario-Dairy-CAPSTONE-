# SARIMA regular-dairy forecasting and ongoing learning

See [learning verification and the 2026 preview](LEARNING_VERIFICATION.md) for
new-data retraining evidence and the remaining prospective-scoring limits.
See [prospective scoring for all nine chart paths](PROSPECTIVE_SCORING.md) for
first-issued predictions, reviewed outcomes and the read-only `/evidence/` API.
Midnight/manual maintenance now includes this evidence stage after projections;
it does not change model selection or production approval.

## Actual invoices only

Forecasting and validation exclude dummy sales. The former synthetic experiment
is disabled at the user's request: its API returns HTTP 410 and its command
fails before reading data or fitting. Previously saved experiments remain
archived; their metrics are not current forecasting evidence.

## Real-data forecasting

The retired forecasting engine is removed. Its original source, database and
results remain in `backups/forecasting-reset-20261022-142212/`. Historical
migrations 0001–0005 preserve the upgrade chain; migration 0006 adds the new
monthly learning lifecycle without deleting business records. A second verified
source/database backup precedes this implementation in
`backups/model-build-20261022/`.
The SARIMA priority revision has its own verified pre-change logical database
and source backup in `backups/sarima-priority-20261022/`.

The replacement now fits and retains model versions, performs past-only rolling
validation/evaluation, issues immutable first-day shadow predictions when data is
current, scores them after real outcomes arrive, and applies publication gates.
Its target is **strictly below 15% monthly regular-revenue WAPE**, not an accuracy
promise or a target for feeding, quantities, weekly totals or yearly totals.

## Rough projections for every graph view and horizon

`GET /api/forecasting/chart/?period=weekly|monthly|yearly` includes
`views.combined|regular|feeding.provisional_forecast` independently of approved
`forecast`. These actual-data estimates provide 13 future complete weeks,
12 months, and 2 calendar years. They remain available with stale/limited history,
explicitly marked provisional, never approved or promised below 15% error.

`forecasting/provisional.py` fits four component models (two segments × weekly /
monthly); annual values sum a single-origin twelve-month path and Combined sums
matching component targets. Missing invoice periods are NaN observations in the
state-space likelihood, not zeros; no pre-feeding padding is used. Only past
classified actual dairy invoices enter fitting. No synthetic data or unclassified
Toned Milk revenue is included.

The small SARIMA family comprises local-level `(0,1,0)`, AR(1) `(1,0,0)` and,
when at least two seasons of observed periods exist before validation,
`(1,0,0)(1,0,0,s)` (`s=12` or `52`). The first two are nonseasonal SARIMA
special cases for sparse data. Earlier validation selects a configuration; a
later single-origin test is separate, never used to choose it. Candidate/fit
failures are retained; fewer than six observed periods or no valid fit withholds
the component rather than inventing values. No baseline replaces SARIMA.

Portable model parameters and missing observations are retained in immutable
`ProvisionalForecastRun` JSON. Source/target fingerprints and a checksum prevent
serving mismatched or corrupt evidence. Refresh rechecks source identity under
transaction locks before saving. The midnight Asia/Manila worker refreshes after monthly and
weekly research; unchanged observations reuse states without optimization.

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py refresh_provisional_forecasts --output backups/provisional-export
```

GET reads saved projections only. Conditional model intervals use nominal 90%
bounds but are uncalibrated; annual and combined ranges sum component bounds
and are not calibrated joint confidence intervals. Long stale-data gaps and
unknown business/program changes make projections especially rough. Annual and
combined error metrics remain null without genuine corresponding evaluation.
Older approved-model research and publication gates remain separate and unchanged.
See [frontend contract](../FRONTEND_HANDOFF.md) for graph fields and rendering.

## Current result and its limits

The primary forecasting family is **SARIMA**, under engine version
`regular-monthly-sarima-v2`. The refreshed source has 36 consecutive
regular-sales months (2023–2025). Five fixed monthly SARIMA configurations use
annual seasonality (period 12): `(0,0,0)`, `(1,0,0)`, `(0,1,0)`, `(1,1,0)` and
`(0,1,1)`, each with seasonal order `(1,0,0,12)`. The two undifferenced models
include an intercept. The search stays small because the earliest validation
fold has only 18 months, providing limited evidence for annual seasonality.

Nine comparison methods are also evaluated: previous month, trailing three/six-month mean,
trailing three-month median, same month last year, fitted exponential level,
fitted damped trend, and two standardized ridge autoregressions. The regressions
learn from lagged revenue, recent changes, trailing mean, and known calendar
features; regularization strengths are fixed before evaluation. All fitted
parameters are re-estimated from available earlier months, not reused forever.

The selected primary model is **SARIMA(0,1,0)(1,0,0,12)**, chosen from SARIMA
configurations on July–December 2024 validation (9.9104% WAPE). Its
January–December 2025 rolling test WAPE is **10.6433%**, against **11.381%** for
the separately selected previous-month baseline. The baseline has a lower
validation error; it remains a comparator because SARIMA is the required primary
family. No test winner or baseline is substituted if selected SARIMA fitting
fails. The previous general-model run remains retained as version 1; its
11.381% score was a baseline result, not a SARIMA score.

Those are retrospective research results. The 2025 period was already inspected
during source cleanup; it is not an untouched acceptance test. Actual sales stop
in December 2025, so the saved next-month research point refers to January 2026.
It is not a forecast for the current 2026 month. Current serving reports
`stale_data`, with no live forecast. No artificial 2026 data is used to bridge the
gap, and no production model is currently approved.

## Data and fitting rules

- Read only real, non-voided sales before the first day of the current business
  month. Current-month partial totals are never training labels.
- Apply the reviewed dairy policy in `sales/importers/dairy_policy.json`.
  Pastillas/PDL, Pastiroll, Polvoron and other excluded products stay excluded.
  New live mixed invoices contribute only their approved dairy revenue.
- Exclude exact reserved demo references `2026:` plus six digits. Updating demos
  does not change actual model input or the real-sales source revision.
- Separate regular, feeding and unclassified sales. Feeding, unresolved dates,
  missing identities/prices, and held mixed invoices are not regular labels.
- Preserve written/inherited source dates with audit flags; do not silently
  assign the unlabelled January Toned Milk row to PCC-UPLB or a feeding program.
- Work on the latest contiguous run of observed regular months. Missing months
  are not filled as zero. Explicit, source-reviewed zero months are supported.
- Pending invoices are revenue; changing payment status does not change labels.
  Backdated sales, voids, amount and classification corrections change revisions.

With at least 36 completed contiguous months, use six earlier validation months
and twelve later test months, leaving at least eighteen initial training months.
Every fold refits using only months before its target. Choose the primary SARIMA
configuration and, separately, the best simple baseline on validation WAPE alone.
Test results for alternative models
are exported as diagnostics. A selected model that fails later fitting is held
out; a test winner is not substituted. Refit the selected definition on all
available completed history to produce one next-month research point.

Fitted model state is finite JSON with a SHA256 checksum, not executable pickle.
Signed zero is canonicalized before hashing so PostgreSQL JSONB normalization
cannot invalidate an unchanged fitted artifact.
The SARIMA state includes orders, trend, normalized history and scale, fitted
parameters/names, library version, convergence results and optimizer warnings.
Predictions can reconstruct the Kalman filter using saved parameters without
running optimization. Fitting normalizes currency values, enforces stationarity
and invertibility, limits BLAS to one thread, and uses bounded L-BFGS fitting
with one bounded Powell retry if necessary. Nonconverged/nonfinite fits are
rejected; fold diagnostics retain retry outcomes and warnings. A checksum-valid
baseline artifact cannot qualify as a SARIMA deployment or prospective issue.
The displayed
range summarizes prior one-step absolute errors; it is **not** a calibrated
probability interval. Only one-month-ahead regular revenue has been evaluated.

## Learning, issued predictions and promotion

`MonthlyModelRun` is versioned by engine, source, coverage revision and monthly
cutoff. Unchanged data reuses the saved run without fitting. A new completed
month or correction creates a new immutable fit; old fits remain available.
Fitting runs offline; a short commit lock rechecks the source before saving.
Concurrent sales/coverage changes abort an inconsistent run.

On the first business-calendar day of a target month, current complete history
can create one shadow `MonthlyIssuedForecast` per engine/month. Its model,
amount, baseline and issue timestamp are retained. Historical commands never
manufacture past issue timestamps. A correction never overwrites an issued
prediction. Mid-month fitting can create a research run but cannot fabricate a
first-day issue. On a later completed-month refresh, `MonthlyForecastScore`
records actual amount/error and coverage status. Corrections create new score
revisions while retaining old actuals and scores.

Source coverage is an explicit admin attestation per month and exact source
revision, not an inference from record counts. Corrections invalidate an old
attestation. Resolve nonzero unclassified entries before attesting; reconcile
held/excluded original workbook rows as well. Empty regular months require an
explicit `--confirm-zero` assertion. The code does not automatically certify the
historical dataset or reinterpret held source amounts.

Automatic publication requires all of the following:

1. A current, checksum-valid fit, with the latest twelve training months reviewed.
2. Twelve retrospective test months below 15% WAPE, no worse than the baseline
   selected on validation.
3. Twelve consecutive recent, reviewed **prospective** issued months below 15%
   WAPE and no worse than their originally issued baseline predictions.
4. Valid first-day issue timestamps and past-only model cutoffs, with the most
   recent closed month represented in prospective evidence.

Only current issued predictions from an approved deployment are served. There
is no forced promotion switch. `MonthlyPromotion` retains an activation trail;
explicit activation/rollback must pass the same source, cutoff and accuracy
checks. An old model cannot be activated against a different month's data. A
retrospective score below 15% alone never approves a production forecast.

See [the October 22 horizon investigation](INVESTIGATION.md) for multi-step
accuracy, feeding limitations and the next validation improvements.

## On-demand refresh API and background jobs

Admin `POST /api/forecasting/refresh/` with an empty body starts a background
maintenance job and returns HTTP 202 with `job_id` and `status_url`. It runs
patterns, monthly learning, weekly research, all-view provisional forecasts,
and prospective issue/scoring maintenance.
`GET /api/forecasting/refresh/` returns the latest job; `GET /api/forecasting/refresh/<id>/`
returns its status/progress/results. Reads are uncached and never fit. Starts
are limited to one per minute per account; status polling uses the existing
expensive-read limit (30/minute). See the frontend handoff for button behavior.

Apply migration `forecasting.0010_forecastrefreshjob`. After the job creation
transaction commits, the backend starts its own venv Python in a detached child:

```bash
venv/bin/python manage.py run_forecast_refresh --job-id JOB_ID
```

This command is an internal runner; omit `--job-id` for a tracked scheduled/manual
CLI maintenance pass. No shell strings or user-selected commands are executed.
The child retains status in PostgreSQL and writes a private worker log under
`backups/forecast-refresh/job-<id>.log`. The API does not run training in a request
thread. The hosting process must permit child processes; launch failure is saved
as a failed job, not silently reported as completed. A small daemon thread only
reaps the child; it does not perform training.

Manual and midnight maintenance share a PostgreSQL session advisory lock over
all phases. A partial unique database constraint permits one queued/running job.
Repeated starts reuse that job. Scheduled maintenance can adopt a queued manual
job and skips when another worker owns the lock. Computation holds no long-lived
SQL transaction/table lock; existing short source-checked commits remain intact.
Job status retains partial completed results and safe error codes if a phase
fails. Operators can inspect the private log for details.

When no worker owns the lock, an abandoned running job becomes failed on the next
status/start/maintenance check. A queued job that never launched times out after
ten minutes; a retry creates a new job. A live worker is not expired simply
because fitting takes a while. The midnight oneshot command uses the same tracked
runner; it still runs only at the configured midnight time.

A refresh fits/reuses **eligible completed-period actual data**, not the current
partial week/month. Existing source/coverage signatures trigger new model versions
after eligible data corrections; unchanged signatures reuse models. This does
not force redundant fitting, include dummy data, overwrite issued predictions,
attest ledger coverage or bypass publication gates. Source changes during a fit
are handled by the existing revalidation checks; retry after data settles.

## Commands and automatic maintenance

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py refresh_forecast_models --output backups/current-monthly-model
venv/bin/python manage.py refresh_forecast_models --as-of 2025-07-01 --output backups/historical-model
venv/bin/python manage.py watch_forecast_models --once
venv/bin/python manage.py watch_forecast_models
```

The watch command refreshes descriptive studies and maintains trained models,
shadow issues and actual-error scores. It reuses unchanged fits. Database/source
races are reported and retried at the next scheduled run. The default long-running
watch command waits until 00:00 Asia/Manila before its first run and recalculates
the next midnight after work finishes, avoiding interval drift. `--once` runs
immediately for the timer or manual maintenance; an explicit `--interval` remains
an operator override rather than the default schedule. Future `--as-of` values are
rejected. Outputs include full model JSON, candidate comparisons, and selected
validation/test prediction CSVs, stored privately.

After actually reconciling an original completed month, an administrator can
record that evidence (do not run this merely to bypass the gate):

```bash
venv/bin/python manage.py verify_forecast_coverage 2025-12-01 --admin-username ADMIN --note 'Original monthly sales ledger reconciled, including exclusions and held rows'
venv/bin/python manage.py activate_forecast_model MODEL_ID
```

No coverage attestations have been fabricated for the current dataset. Source
review and genuine future real-sales outcomes are still needed for promotion.

Daily midnight Asia/Manila user-systemd service/timer files are in `forecasting/deploy/`. Their paths
match this workstation's `~/Documents/CodingProjects/SarimaHoes`; adjust them if
installing elsewhere. The service uses the backend virtual environment and .env.

```bash
systemctl --user link "$PWD/forecasting/deploy/rosario-forecast-learning.service" "$PWD/forecasting/deploy/rosario-forecast-learning.timer"
systemctl --user daemon-reload
systemctl --user enable --now rosario-forecast-learning.timer
systemctl --user start rosario-forecast-learning.service
systemctl --user status rosario-forecast-learning.timer --no-pager
journalctl --user -u rosario-forecast-learning.service -n 30 --no-pager
```

The timer uses `OnCalendar=*-*-* 00:00:00 Asia/Manila`, one-second timer
accuracy, no randomized delay and no boot trigger. `Persistent=false` avoids
launching a missed run during daytime: the next run waits until midnight.
The timer runs while the user systemd manager is available. This does not enable
system-wide linger or guarantee operation while the workstation is powered off.
Disable with `systemctl --user disable --now rosario-forecast-learning.timer`.

## Read APIs and frontend handoff

All endpoints are admin-only; GET requests read saved results and never fit.

- `GET /api/forecasting/models/`: current model status, every retained version,
  selected candidate, candidate validation/test metrics, explicitly labelled
  research point, source/cutoff, checksum and live quality-gate reasons.
  `model_family` is `SARIMA`; `sarima` gives orders, trend, convergence and
  fitting diagnostics. Candidate `role` distinguishes primary SARIMA from
  comparisons. Version entries include their engine, including earlier engines.
- `GET /api/forecasting/forecast/?period=monthly`: compatibility forecast route.
  Returns `stale_data`, `refresh_required`, `research_only` or `ready` as appropriate.
  Live forecast points remain empty unless a current issued model is approved.
  Weekly/yearly requests are withheld as `unsupported_horizon` after model fitting.
- `GET /api/forecasting/patterns/`: retained descriptive studies. Their original
  exclusion/coverage fields describe the study's creation snapshot; current model
  gates and prospective evidence are reported by `/models/`.

The forecast report/PDF uses the same status and labels any approved value as
**regular dairy revenue**. Feeding and combined revenue are never silently set to
zero or represented as validated forecasts. The frontend repository is untouched.
See `FRONTEND_HANDOFF.md` for response fields and handling of research previews.

## Method references

Past-only evaluation follows the authors' [time-series cross-validation discussion](https://otexts.com/fpp3/toolbox.html#time-series-cross-validation).
SARIMA uses the installed statsmodels 0.14.6 API, checked locally, documented in
[SARIMAX](https://www.statsmodels.org/stable/generated/statsmodels.tsa.statespace.sarimax.SARIMAX.html)
without exogenous regressors. The `seasonal_order` period is 12 for monthly data.
Comparison smoothing uses the installed statsmodels API, documented
in [SimpleExpSmoothing](https://www.statsmodels.org/stable/generated/statsmodels.tsa.holtwinters.SimpleExpSmoothing.html)
and [Holt](https://www.statsmodels.org/stable/generated/statsmodels.tsa.holtwinters.Holt.html).

Backups are private logical Django data dumps, not native PostgreSQL cluster
backups. Verify hashes and restore into a separate empty database at the matching
archived schema before planning live recovery. Schema reversal does not restore
deleted legacy forecast rows. Historical research/model/score rows are retained.

## Recorded-invoice feeding diagnostics

Admin-only `GET /api/forecasting/feeding/` computes an actual-invoice audit
through the last completed month, without fitting or issuing forecasts. It
exposes descriptive source-label groups, monthly counts/revenue, unverified
gaps and readiness limitations. The audit updates as actual invoices arrive.

Export actual evidence with:

```bash
venv/bin/python manage.py investigate_feeding_sales --output backups/feeding-research-20261022/actual-results
```

See [FEEDING_RESEARCH.md](FEEDING_RESEARCH.md) for the limited feeding history.
Missing invoices are not verified absence of demand; no current feeding model
is approved. The regular SARIMA model and production gates are unchanged.

## Three line-graph views

Admin-only `GET /api/forecasting/chart/?period=monthly` returns `views.combined`,
`views.regular` and `views.feeding` with aligned `history` rows (`date`, `actual`,
`predicted`, invoice count and record status), independent historical metrics,
approved live points and availability status. No fitting occurs on GET.
Combined recorded totals sum classified invoices. Missing invoice periods are
null rather than verified zero demand, and unclassified/dummy sales are excluded.

The regular prediction line uses saved, past-only test folds only when its source
revision and recorded monthly actuals match. Invalid/old comparisons are withheld.
Historical metrics are recomputed from those matched folds. A stale live status
does not erase a valid historical comparison. Feeding has no validated prediction
line; combined forecasts and metrics remain unavailable until both components
are available. No regular-only values are substituted into combined predictions.
See `FRONTEND_HANDOFF.md` for the three-tab graph contract.

The chart endpoint accepts `period=weekly|monthly|yearly` (default monthly), with
all three sales tabs available for each period. Invoice dates determine recorded
weekly (Monday–Sunday), monthly and annual sums. Cache requests by period. Null
invoice periods remain gaps. Weekly research predictions now use their own fitted model and saved
past-only backtest; the monthly model is not split across weeks. Yearly regular
predictions sum exactly twelve valid monthly rolling backtests where available;
`prediction_basis` explicitly identifies this aggregation, not a year-ahead
forecast. Weekly metrics are computed from weekly backtests; yearly metrics remain null.
No live monthly point is relabelled as another horizon. `validated_forecast_periods: [monthly]` describes
model evidence; `available_periods` keeps all three history selectors visible.
The legacy forecast/report endpoint's publication rules remain unchanged.

## Actual-data weekly SARIMA research

Migration `forecasting.0008_weeklymodelrun` retains weekly research separately
from monthly deployment/coverage/issued scores. `regular-weekly-sarima-v1` uses
actual regular dairy invoices only, before the current Monday. Edge weeks that
cross the available source-month span are dropped, and missing weeks split the
history rather than being filled with zeros. Feeding/unclassified/demo sales
never become weekly regular labels.

At least 130 consecutive full calendar weeks are required: 104 initial training,
13 earlier validation and 13 later test weeks. The fixed primary pool is
SARIMA(0,1,0), (1,0,0) and (0,1,1), each with seasonal (1,0,0,52). The AR(1) model
has a constant; the differenced models do not. Each fold refits using only earlier
weeks. Select SARIMA and, separately, one of previous-week, trailing-four-mean and
52-week-lag baselines on earlier validation WAPE. Never reselect on test results
or silently replace a failed selected SARIMA. The 52-week annual period is an
approximation, not proof of a stable seasonal feeding schedule.

The recorded source supplies 156 usable consecutive weeks (January 2, 2023–
December 22, 2025). Initial research selects SARIMA(1,0,0)(1,0,0,52). Test WAPE is
26.8135% over 13 weeks versus 20.2992% for the validation-selected comparator.
The weekly model does not beat that baseline and is not approved for production.
The monthly model's 10.6433% result and 15% research target are unchanged.

```bash
venv/bin/python manage.py refresh_weekly_forecasts --output backups/weekly-sarima-20261022/results
```

The existing nightly `watch_forecast_models` worker also refreshes weekly research.
Unchanged revisions reuse saved results; changed cutoffs with identical weekly
inputs reuse fitted parameters without optimization. New observations/corrections
create retained revisions. Sources and cutoff are rechecked under short commit
locks after fitting. Artifacts are portable JSON with checksums, never pickles.
Read-side reconstruction uses filtering rather than optimization.

Admin-only `GET /api/forecasting/weekly/` exposes the weekly evidence and a clearly
non-live research point. `GET /api/forecasting/chart/?period=weekly` exposes matched
historical weekly predictions for the Regular sales line. Both GETs only read;
old revisions, invalid artifacts and inconsistent comparisons withhold predictions.
Current actual history is stale, `production_ready` is false, and live weekly
`forecast` remains empty. There is no weekly deployment or fabricated prospective
acceptance record. Feeding and combined predictions remain unavailable.

Backup/evaluation: `backups/weekly-sarima-20261022/`. Statsmodels' documented
[`SARIMAXResults.forecast`](https://www.statsmodels.org/stable/generated/statsmodels.tsa.statespace.sarimax.SARIMAXResults.forecast.html)
provides the next step from each fitted origin; local installed signatures and
serialization were checked. This adds weekly research without changing the
monthly fitting engine. Genuine year-ahead evaluation and feeding modelling
remain further work; annual chart values are still sums of monthly predictions.
