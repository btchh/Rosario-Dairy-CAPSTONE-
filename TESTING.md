# Backend verification

Latest pre-push verification (2026-10-22): **420 tests passed** in 22.375 seconds,
covering forecast chart/actual-only guards/feeding studies and existing sales,
reports, inventory and config checks. Django system checks passed and the migration
drift check found no changes. A fast password hasher was used only in the test
process; production password settings are unchanged.

## Inventory test types and current frontend handoff — 2026-10-22

`inventory/tests.py` uses the typed DRF client/case adapters in
`config/test_support.py`. They retain normal request and transaction behavior,
describe DRF response payloads at the test boundary, and prevent generic
`Client`/`HttpResponse` inference from hiding `force_authenticate`/`data`.
Serializer results are narrowed where the test knows it is using a single
instance; decimal fields receive `Decimal`, related IDs use their model relation,
and query-added expiry annotations are read explicitly. No blanket Pylance rule
suppression is used. These adapters are for DRF endpoints, not HTML/streaming tests.

All 120 inventory tests passed before and after the initial typing changes.
The final combined backend suite passed 404 tests in 28.508 seconds.
The CLI type check reports none of the supplied response/client/model-attribute
diagnostics, and no diagnostics in the new adapter. Plain Pyright still reports
other Django dynamic-manager/decorator inference issues; this is not a claim
that the entire backend passes a strict static type check.

`FRONTEND_HANDOFF.md` now records frontend commit `393383a3`, implemented report
date controls/service/hooks/cache behavior, and remaining forecast-contract
differences. The frontend was inspected read-only.

## Forecasting replacement (2026-10-22)

The legacy forecasting engine has been backed up and removed. New pattern-study
checks cover actual-sales filtering, calendar cutoffs, regular/feeding separation,
source revisions, payment-invariant revenue, all-customer reconciliation,
chronological research benchmarks, expanding observation windows, immutable study
history, concurrent ledger changes, offline refresh and admin-only endpoints.

```bash
venv/bin/python manage.py check
venv/bin/python manage.py makemigrations --check --dry-run
venv/bin/python manage.py test forecasting reporting sales.tests.StaffEncodingTests sales.tests.EncodingPaymentTests config.tests.DataCacheTests config.tests.SecurityHardeningTests --keepdb --noinput
```

Database schema setup uses migrations 0001–0005. Migration 0005 deletes derived
legacy forecast tables. Back up source and database data before applying it;
tests must run on the Django test database, not the development data.

Research smoke test (writes a new study or reuses the unchanged revision):

```bash
venv/bin/python manage.py refresh_forecast_patterns --output backups/current-pattern-study
venv/bin/python manage.py watch_forecast_patterns --once
```

Verify the forecast endpoint returns an empty prediction list and a rebuilding
message. Verify the patterns endpoint returns current findings after refresh and
`refresh_required` after a completed source change. No research result should be
presented as a validated <15% forecast. This research phase does not install a
scheduler, train a new model, publish predictions, or alter frontend files.

Report tests also verify date ranges, complete record lists, and page breaks that
keep headings with table headers and the first content row.


## Dairy-only source refresh

```bash
venv/bin/python manage.py test sales.test_dataset_refresh forecasting sales.tests.AnnualSalesParserTests sales.tests.AnnualSalesLoaderTests --keepdb --noinput
```

Refresh tests cover the source formula invoice boundary, SI preservation,
proportional discounts on mixed receipts, user-specified sweets exclusions,
unreviewed products, invoice-only totals, unclassified entries, preservation of
manual sales/current stock, repeat-run receipt/line identity, and atomic rollback.
Forecast tests also check that new live mixed/non-dairy invoices follow the
product policy and that item corrections invalidate source revisions.

Run the preview/apply commands in `sales/DATASET_REFRESH.md` against original
workbooks. Confirm all 36 sheet totals reconcile before filtering; retained dairy
plus exclusions/holds must equal original eligible source revenue. Verify the
apply manifest's protected-row fingerprints and backup hashes. No frontend
changes or model accuracy claims are part of this refresh.

2026-10-22 verification: 138 integration tests passed across refresh, forecasting,
reporting, annual import, encoding/payments, cache and security tests. Applied
source count/revenue/product-policy reconciliation and original workbook hashes
passed. Live forecast/pattern endpoints, date-ranged customer preview/PDF and
SI03744 receipt returned HTTP 200; the SI remains recorded at its retained dairy
amount. Evidence is in the private dataset-refresh findings directory.


## Demo dataset refresh

```bash
venv/bin/python manage.py test sales.tests.Populate2026SalesTests forecasting reporting sales.test_dataset_refresh --keepdb --noinput
```

Demo tests cover classified dairy-only source selection, unresolved/manual source
exclusion, stale non-dairy basket rejection, deterministic generation and net
receipt/order amounts, exact demo-namespace replacement alongside real 2026
sales, preservation of the forecasting revision and inventory, manual/live-stock
protection, verified backups, and rollback if insertion fails. Preview/apply
commands and backup recovery guidance are in `sales/DATASET_REFRESH.md`.

2026-10-22 demo refresh verification: all 88 tests in the command above passed.
Post-commit checks verified 1,526 dairy-only demo receipts, 7,982 lines, exact
receipt/order sums, backup hashes, preserved real-sales source revision, and all
4,835 historical dairy receipts. Demo receipts remain excluded from forecasting.


## Monthly model training and learning lifecycle

```bash
venv/bin/python manage.py test forecasting reporting sales.test_dataset_refresh sales.tests.AnnualSalesParserTests sales.tests.AnnualSalesLoaderTests sales.tests.Populate2026SalesTests sales.tests.StaffEncodingTests sales.tests.EncodingPaymentTests config.tests.DataCacheTests config.tests.SecurityHardeningTests --keepdb --noinput
```

`forecasting.test_learning` covers JSON fitted-state round trips for all nine
candidates, past-only folds, validation-only selection when test values change,
advancing windows, missing/zero months, immutable fits and issued predictions,
partial-month exclusion, source races, versioned corrected actual scores,
coverage invalidation and verified zero months, retrospective-only promotion
rejection, successful promotion with twelve reviewed prospective months,
negative prospective error/late-issue/coverage gates, checksum failure, admin
permissions, offline-only fitting and command validation. Existing reporting,
encoding, demo-exclusion, cache and security tests remain in the integration run.

Real-data checks should verify `forecasting.0006` is applied, sales/catalog/stock
rows match the pre-model backup, the first model retains 36 complete real months,
and no demo record or unresolved Toned Milk entry enters regular training.
Confirm the first research target is January 2026 while actual history stops at
December 2025; the current forecast endpoint must show `stale_data` and no live
points. Exercise model status and forecast preview/PDF endpoints, repeat offline
refresh to confirm reuse, and inspect the hourly maintenance service/timer.
Do not attest coverage, fabricate prospective issues, or force promotion just
to make a live forecast appear during a smoke check.

2026-10-22 model implementation verification: all 163 integration tests passed.
Six live API/PDF checks passed; exact pre/post business-row comparison and source
revision checks passed. The hourly learning timer is enabled and its first
service run exited successfully (status 0), reusing the saved model. Coverage,
prospective issue and production deployment tables remain empty for the real
stale dataset. No test fixtures or fabricated acceptance evidence were inserted
into the live database. Private results: `backups/model-build-20261022/`.


2026-10-22 missing-identity fix: 80 targeted import, demo, forecast and report
tests passed, plus the focused sales-ledger/unassigned-report regression. The
Toned Milk source row stays unclassified, the customer/SI API fields return
null, and a repeated historical refresh preserves that identity. Live checks
verified inclusion in unassigned report revenue, exact preservation of all other
transactions, and the same real forecasting source revision. The pre-change
backup and verification are in `backups/toned-milk-null-identity-20261022/`.


## SARIMA primary model

Run the monthly-model integration command above. The same learning tests now
exercise five annual SARIMA configurations and nine comparisons, saved-parameter
Kalman filtering without optimization, past-only SARIMA folds, and rejection of
insufficient seasonal history. Additional regressions prove that a better
baseline cannot replace primary SARIMA, all failed SARIMA fits produce no
baseline fallback, a failed final refit is withheld, and even a checksum-valid
baseline artifact cannot pass the SARIMA publication gate. Positive prospective
promotion fixtures use real fitted SARIMA artifacts in the isolated test DB.
Additional checks reject nonconverged optimization after both bounded retries
and verify that nested signed-zero values survive PostgreSQL JSONB checksum
round trips without bypassing integrity checks.

For real-data verification, retain general model 1 and confirm current engine
`regular-monthly-sarima-v2` selects `sarima_010_100_12` in model 2, with seasonal
order `[1,0,0,12]`, converged parameters and a valid JSON checksum. The earlier
validation WAPE is 9.9104%; the twelve 2025 rolling test months yield 10.6433%
SARIMA WAPE and 11.381% baseline WAPE. These are retrospective results, never
prospective acceptance. Reuse unchanged data without refitting, verify exact
business-row/source-revision equality against the pre-change backup, and smoke
test model/forecast/report APIs and PDF. Current serving must remain stale with
no issued forecasts, coverage attestations, or deployment fabricated. Confirm
the hourly timer is enabled after validation. Private evidence and backups are
in `backups/sarima-priority-20261022/`.


2026-10-22 SARIMA priority verification: all 170 integration tests passed. Six live
API/PDF checks passed; the current engine retained SARIMA model 2, preserved
model 1, and reused unchanged inputs without optimization. Business sales and
inventory match the pre-change backup; external login/session changes were
recorded and preserved. The real source revision is unchanged. The hourly timer
is enabled and maintenance succeeded with no fabricated coverage, issues or
deployments. Complete evidence: `backups/sarima-priority-20261022/`.


## Actual-only forecasting and feeding diagnostics

```bash
venv/bin/python manage.py test forecasting.test_demo forecasting.test_feeding forecasting.tests sales reporting inventory config --keepdb --noinput
```

The former synthetic test path is disabled. Guard tests verify its input/training
entry points fail without queries, archived scores are withheld and retained,
the command does not export, and the admin API returns HTTP 410 even when an old
synthetic payload exists in cache. Dummy sales remain excluded from actual
snapshots. Test fixtures verify policy; no dummy dataset is used to measure accuracy.

Feeding tests cover actual-only segment selection, gaps without zero-demand
attestation, label groups with original aliases, empty history, actual JSON/CSV export and admin-only completed-month access
without fitting. The final actual-only/core suite passed 404 tests in 23.900 seconds, using a
fast password hasher only in the isolated test process. Runtime hashing is unchanged.

Actual exports: `backups/feeding-research-20261022/actual-results/`. Compare all
forecasting rows with the pre-change private dump, and live sales/order/item/batch
fingerprints and actual revision with their baseline. The new API and command
must not create forecasting evidence rows or alter existing actual models.

## Three forecast line-graph views — 2026-10-22

`forecasting.test_chart` covers the combined/regular/feeding calendars and recorded
sums, excluded unclassified revenue, null gaps, separate backtest/live results,
source-correction withholding, invalid fold/origin rejection, component forecast
withholding, empty history and admin-only GET without fitting/publication.
Unsupported chart periods (such as daily) and synthetic scopes return HTTP 400.

The combined core suite passed 413 tests before the final query-validation
addition. All 10 final chart tests passed after it. Live admin smoke checks
confirmed three views, 12 historical regular prediction points, 10.6433% WAPE,
no feeding/combined predictions, and unchanged model/issued/experiment counts.
A standalone three-tab line-graph preview is saved privately under
`backups/frontend-integration-preview-20261022/three-views.html`. No frontend
source files changed. No synthetic sales were used for any displayed score.

## Weekly, monthly and yearly chart controls — 2026-10-22

The chart API accepts all three periods for each of the three sales views.
Tests cover cross-year Monday–Sunday buckets, leap-day boundaries, totals and
calendar reconciliation, null invoice gaps, twelve-month prediction aggregation,
incomplete annual comparisons, period-specific request/cache selection, and
withholding live monthly points at other horizons. The full core suite passed
419 tests before the final partial-period test; final focused chart checks also
cover cutoff clipping and partial annual labels.

Live smoke checks verified all nine view/period combinations, equal recorded
revenue totals across groupings, unchanged model/issued/experiment counts,
monthly historical WAPE 10.6433%, zero fabricated weekly predictions and one
annual sum built from the twelve saved 2025 monthly predictions. No fitting or
source-data changes occurred. The standalone preview now has both the three
sales tabs and Weekly / Monthly / Yearly controls; frontend source is untouched.

## Actual-data weekly SARIMA research — 2026-10-22

Apply `forecasting.0008_weeklymodelrun`, then run:

```bash
venv/bin/python manage.py test forecasting.test_weekly forecasting.test_chart forecasting.test_demo forecasting.test_feeding forecasting.tests sales reporting inventory config --keepdb --noinput
```

The verification run also included the existing monthly worker lifecycle test:
`forecasting.test_learning.MonthlyLifecycleTests.test_commands_support_repeatable_maintenance_and_safe_arguments`.
**435 tests passed in 42.307 seconds**, with the fast password hasher confined to
the test process. Django checks and migration-drift checks passed.

Fourteen weekly tests cover full-week boundaries, missing versus recorded-zero
weeks, segment separation, insufficient histories, past-only fitting, validation
selection independent of test outcomes, selected-fit failure withholding, real
SARIMA JSON replay without optimization, calendar cutoffs, immutable corrected
versions, reuse across cutoffs, source/cutoff races, corrupt model/comparison
withholding, admin access, graph integration, command export and scheduled refresh.
No fixture observations are inserted into the real evaluation dataset.

Live actual-data checks verified 156 weekly observations, 13 held-out weekly
predictions, 26.8135% SARIMA WAPE and 20.2992% baseline WAPE. The model does not
beat the comparator and is research only. Monthly historical WAPE remains
10.6433%. All preexisting forecast rows and the actual source revision match the
pre-change backup. Worker maintenance reused both models without fitting, and
saved weekly state reproduced its next research value without optimization.
Coverage, monthly issued scores/deployments, source sales and inventory are
unchanged. Private evidence: `backups/weekly-sarima-20261022/`.

## Rough predictions for all nine combinations — 2026-10-22

Apply migrations through `forecasting.0009_provisionalforecastrun` and run
`refresh_provisional_forecasts` offline. The chart API now includes separate
`provisional_forecast` points for Combined / Regular sales / Milk feeding at
weekly (13 points), monthly (12 points) and yearly (2 points) intervals.
Approved `forecast` arrays and publication gates remain separate.

The full integration run passed **445 tests in 39.444 seconds**, including the
monthly worker lifecycle, forecasting, sales, reports, inventory and cache tests.
Django system checks and migration-drift checks passed. Ten new tests cover
unknown observations versus zeros, exclusion of unclassified records, portable
SARIMA replay without optimization, future dates across all nine combinations,
component reconciliation, single-origin annual paths, seasonality eligibility,
past-only validation/test selection, insufficient/failed fits, signed-zero
checksum stability, source races, immutable reuse, corruption withholding,
stale history and authenticated chart GET without fitting. Test fixtures never
enter the real evaluation dataset.

The actual-data run produces all nine prediction series. These rough models use
a different single-origin test from the retained monthly and weekly rolling
research. Current provisional held-out WAPE is 26.4739% regular monthly,
22.7002% regular weekly, 86.8821% feeding monthly and 72.091% feeding weekly.
These errors are substantial; no below-15% or annual/combined accuracy claim is
made. Feeding currently selects a nonseasonal local-level SARIMA special case;
seasonality is not established from its short invoice history. Existing monthly
rolling research WAPE remains 10.6433% and weekly research 26.8135%.

Private source/model backup, actual projections and verification are under
`backups/provisional-all-views-20261022/`. No frontend files were changed and
nothing was pushed.

## Midnight maintenance and horizon investigation — 2026-10-22

This supersedes the hourly schedule described in earlier entries. The active
user-systemd timer now uses `OnCalendar=*-*-* 00:00:00 Asia/Manila`, with no boot
trigger, no randomized delay, one-second timer accuracy and `Persistent=false`.
Both the repository unit and installed linked unit were verified after reload;
the next scheduled run is October 23, 2026 at 00:00 Manila time. Missed runs wait
for the next midnight. No frontend edits or pushes were made.

The default standalone watcher also waits for Manila midnight and recalculates
the next deadline after work. `--once` still runs immediately for systemd/manual
maintenance; explicit interval overrides remain supported.

**45 targeted tests passed in 22.830 seconds**, covering scheduling, provisional
predictions, weekly research, chart API and monthly worker lifecycle. Four new
schedule tests cover UTC-host/Manila-midnight conversion, year rollover,
recalculation after work, immediate one-shot operation and failure retry at the
next scheduled run. Django checks and migration-drift checks passed.

Read [the horizon investigation](forecasting/INVESTIGATION.md) for the actual-data
comparison. Exploratory fits ran privately without changing selected model rows,
source data or approval records. The same monthly SARIMA configuration reproduces
10.6433% one-step rolling WAPE but has 21.5794% monthly-point WAPE over a single
12-month path; annual-total error is 16.9682%. Feeding has insufficient earlier
observations for a credible twelve-month holdout under the study's minimum.
No exploratory test winner was substituted into serving.

## On-demand forecasting refresh — 2026-10-22

Apply `forecasting.0010_forecastrefreshjob`. Admin POST `/api/forecasting/refresh/`
with `{}` returns 202 and a job status URL; the backend launches its venv Python
child after commit. GET latest/detail status is uncached and never trains. The
frontend button contract is documented in `FRONTEND_HANDOFF.md`; frontend code
is untouched.

The **461-test integration suite passed in 44.315 seconds**, covering new jobs,
midnight scheduling, all provisional/weekly/chart paths, existing monthly worker
lifecycle, sales, reporting, inventory and config/cache behavior. Django system
checks and migration-drift checks passed. The final focused job suite passed
**13 tests in 3.555 seconds**, adding a real pipeline test with a completed-period
correction after that broader run. Fixtures remain confined to the test database.

Coverage includes asynchronous start after commit, status polling, admin/staff/
anonymous access, rejection of user-selected scope/model arguments, start rate
limits, duplicate-job reuse, shared manual/midnight processing, four-stage
progress, source-error/partial-result retention, abandoned-worker recovery,
launch failure, database uniqueness and real cross-connection PostgreSQL advisory
locking. The correction test runs the complete pipeline on test invoices: an
unchanged pass reuses every model, then a corrected completed-month invoice
creates new versions and refits only the changed regular weekly/monthly rough
components. Feeding states and prior versions remain retained, with no fabricated
issued forecast or approval.

A live actual-data smoke test used the admin POST, received 202, launched the real
background child, and polled a succeeded job over HTTP 200. All stages reused
unchanged fits. All nine chart series remain populated, and chart GET was verified
with optimization blocked. Every preexisting forecast row and the source
fingerprint match the pre-request backup. No sales or inventory records were
changed for this live test. Private evidence and backup:
`backups/manual-forecast-refresh-20261022/`; child log:
`backups/forecast-refresh/job-1.log`.

The active midnight timer remains enabled. Scheduled and manual jobs use the same
session lock and tracked runner, so fitting cannot overlap. Completed-period-only
training, actual-data-only rules, immutable issued values and quality gates remain
in force. Nothing was pushed.

## Customer account consolidation — 2026-10-23

**217 sales/reporting tests passed in 23.772 seconds**, followed by the broader
**470-test integration suite in 92.963 seconds**. Django system and migration-drift
checks passed; no new schema migration is required for this change.

Eight new customer tests cover bounded date/day aliases, distinct locations and
feeding programs, exact receipt/order preservation when moving customer links,
canonical ID/contact/active-state selection, idempotence, conflicting contacts,
unconfirmed same-name identities, read-only command preview, verified backup,
rollback on invariant failure, API name normalization, rejection of duplicate
alias creation and preservation of distinct same-name people with contact evidence.
Fixtures remain confined to the test database.

The live consolidation reduced **364 accounts to 330** by merging **34 duplicates
in 21 groups**. Full logical backup and audit are under
`backups/customer-consolidation-20261023-001246-027390/`. The completed manifest
verifies protected table fingerprints are unchanged: the only receipt/order
changes are their customer foreign keys. Original invoice/SI labels, receipt
amounts, payment states, dates, discounts, source segments and inventory are
preserved. Original foreign-key links are recorded for every moved record.
A fresh read-only preview finds no remaining clear merge groups. Ambiguous people
and separate branches/programs are retained for confirmation.

Importer and customer creation now share the same explicit normalization rules;
future dated Ala Eh labels resolve to Ala Eh Festival. See
[sales/CUSTOMER_CONSOLIDATION.md](sales/CUSTOMER_CONSOLIDATION.md).
On-demand refresh job 3 completed after consolidation and all nine forecast
series remain available with aligned customer source references. A live 2025
customer-report check returns one Ala Eh Festival account with all six actual
receipts. No frontend files were changed and nothing was pushed.

## Owner-confirmed customer aliases — 2026-10-23

The owner confirmed preferred names Jamie Ortiz, Cassie Alagar and Sir Sonny,
then Jonny Decena. The shared normalization policy now resolves their listed
variants to those exact names. A second backed-up consolidation merged six more
redundant accounts across these four groups: **330 customers to 324**. Preferred
existing account IDs remain intact. Unknown people and distinct programs/branches
remain separate.

The sales/reporting suite passed **218 tests in 24.123 seconds**; the final
customer-specific suite passed **9 tests in 0.874 seconds** after adding Jonny.
Coverage includes the confirmed mappings and continued separation of unrelated
names. No schema migration is required.

Backup and verified before/after manifest:
`backups/customer-consolidation-20261023-002001-008806/`.
All protected fingerprints match; only intended customer links on receipts and
orders changed. The database has one account per preferred name, none of the
confirmed old aliases as separate accounts, and no remaining clear merge group.
On-demand forecast refresh job 4 completed successfully with aligned customer
source references, and all nine forecast series remain populated. No frontend
files were changed and nothing was pushed.

## SQL dataset backup, 2026 graph and new-data learning checks — 2026-10-23

The full integration suite passed **476 tests in 169.555 seconds**, including the
existing primary-monthly completed-month growth test, worker lifecycle, all-view
provisional/weekly/chart paths, SQL backup tests, sales, reporting, inventory and
cache/config behavior. The preceding focused backup/growth/job/schedule suite
passed **31 tests in 16.223 seconds**. Django checks and migration-drift checks
passed. No live business rows or frontend files were changed; nothing was pushed.

New tests verify that newly completed weeks and months increase both regular and
feeding observed histories and trigger their actual refitting functions, while
partial periods reuse fitted input states and previous JSON artifacts remain
unchanged. A separate eligibility test covers monthly/weekly seasonal candidates
entering the bounded search only after sufficient earlier validation-prefix
observations. These are isolated test fixtures, not dummy observations inserted
into actual model evaluation.

The 2023–2025 data-only PostgreSQL backup at
`backups/actual-sales-sql-2023-2025-20261023/` contains 4,827 receipts and 24,251
receipt lines, 4,822 linked orders/24,239 order lines and required dependencies.
All exported rows were restored in a separate temporary database and their
fingerprints matched. Compressed data matches the SQL SHA256, live exported rows
remain unchanged, and no verification database remains. Tests also cover business
year boundaries, dummy-reference exclusion, null-customer receipts and SQL
roundtrip safety for quotes, Unicode, nulls and decimal money values. This is a
data-only backup requiring the recorded empty application schema, not a native
server/schema/roles dump.

The standalone graph in `backups/forecast-2026-preview-20261023/` projects January
through December 2026 from December 2025 saved training. It includes PNG/PDF,
interactive three-tab HTML, CSV, portable model state and provenance. Regular
uses retained primary seasonal monthly model 4 (36 actual 2023–2025 months);
feeding uses the provisional monthly component from run 3 (10 observed months).
Combined adds matching components. Model optimization is blocked during replay,
and every monthly input/source fingerprint is verified. No 2026 actual or dummy
sale is used. Chromium rendered the interactive graph successfully; the PNG was
visually inspected. All three views have twelve forecast values and reconcile.

Read [forecasting/LEARNING_VERIFICATION.md](forecasting/LEARNING_VERIFICATION.md)
for the distinction between proven automatic retraining and unproven accuracy
improvement. The primary monthly reviewed prospective lifecycle exists; equivalent
prospective scoring for other rough paths remains required work. No future
accuracy or automatic approval is promised by these new-data tests.
