# Backend verification

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
