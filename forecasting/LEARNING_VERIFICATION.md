# Learning behavior and 2026 preview — October 23, 2026

The forecasting system refits models as eligible actual observations accumulate.
It does not learn merely because time passes or because the graph is refreshed.
A successful maintenance job and changed model/source evidence demonstrate an
update; a new model version alone does not establish better accuracy.

## What is automatic and tested

- Midnight Asia/Manila maintenance and admin manual refresh run the same tracked,
  mutually exclusive workflow. Poll the job status: a failed/interrupted run is
  not evidence of successful learning.
- New completed regular/feeding observations change source/input fingerprints.
  Affected component parameters are re-estimated and configurations are selected
  on earlier validation periods; later test observations do not choose a winner.
- Unchanged observations reuse fitted state. A correction can retrain an affected
  segment without refitting unchanged feeding/regular component observations.
- Newly eligible annual seasonal candidates can enter the bounded search. The
  current provisional policy requires two seasons of observed training periods
  **before validation**, not simply two years on the calendar. With its six-period
  validation and test blocks, this currently means 36 observed monthly periods
  or 116 observed weekly periods in the unit tests. Missing periods do not count
  as observations or become zero sales. Eligibility does not guarantee selection.
- Combined future values add matching regular and feeding values; yearly values
  use monthly component paths. Both update with their underlying components.
- Prior model artifacts and issued forecasts are retained, and source revalidation
  prevents saving/serving an inconsistent corrected-data comparison.

The new persistence test inserts January–June observations in the **test database**,
then inserts July observations and exercises the actual refresh function at
successive cutoffs. While July's week/month is unfinished, fitted inputs are
reused. Once its week closes, both weekly components refit (six to seven observed
periods), with monthly inputs unchanged. Once July closes, both monthly components
refit and their training end advances to July; old artifacts remain unchanged.
The separate production-monthly lifecycle test covers the existing 36-month
SARIMA model, partial-month exclusion and a new completed month's model version.

These are test fixtures for software behavior. They are never added to the live
training dataset or used to report real forecasting accuracy.

## What is not guaranteed or implemented yet

More observations can improve estimation or reveal changed behavior, but they can
also worsen measured performance. No fixed percentage improvement is guaranteed.
Configuration choices remain within the explicitly implemented model family;
retraining does not invent new features, contract schedules or model classes.

The primary monthly engine also requires a contiguous completed training span
(or explicitly reviewed zero-coverage months). New invoices after an unresolved
history gap do not automatically restore production eligibility. The provisional
state-space paths can retain unknown internal observations without zero-filling.

The reviewed production publication lifecycle currently covers **regular
monthly** forecasts. All nine provisional chart paths now have a separate
[prospective evidence lifecycle](PROSPECTIVE_SCORING.md): maintenance retains
predictions before their targets start and scores reviewed completed outcomes.
This adds monitoring, not automatic approval for these rough paths. Their
availability is not proof that they improve over time. Retained validation/test
scores remain retrospective. Feeding's sparse, concentrated invoice history is
especially weak evidence for long horizons.

To prove improvement, retain forecasts before outcomes arrive, score each horizon
against later reviewed actuals and a declared baseline, and monitor error/bias and
interval coverage across multiple forecast origins. The additional scorer keeps
these horizons separate and preserves first-issued predictions through refits;
it must accumulate real future outcomes before supporting improvement claims.
Operator coverage review is still needed; no attestations
have been manufactured. The computer/user maintenance service must actually run;
the latest successful job and its model IDs are the operational evidence.

## 2026 graph generated from retained training

The generated graph, HTML, CSV exports and one-off script formerly in
`backups/forecast-2026-preview-20261023/` were removed during the requested
artifact cleanup. The following records that preview's provenance. The database
model runs and actual sales remain available to generate another preview.

Regular uses the retained primary seasonal monthly SARIMA model **4**, fitted to
36 months from January 2023 through December 2025. Feeding uses the saved
provisional monthly component from run **3**, with 10 observed months and four
internal gaps. Combined sums these two paths. All saved inputs are checked against
actual totals/source fingerprints. Optimization is blocked while projecting,
proving the preview uses the retained trained state.

This is a fixed-origin 2026 research projection, not live approval and not the
older 2025 rolling backtest. No 2026 actuals or dummy invoices enter fitting or
evaluation. At generation time the graph API independently selected a simpler
regular model. The v2 graph pipeline now reuses the matching saved primary
seasonal SARIMA for Regular monthly/yearly and their contribution to Combined;
only short/gapped regular histories retain a labelled fallback. The retained
preview's model origin remains December 2025; current graph targets follow the
request date. Feeding is flat because its current
nonseasonal local-level model extrapolates the latest level, without evidence
for future billing events. Bounds are uncalibrated and summed bounds are not a
joint probability interval. The 10.6433% monthly rolling research WAPE does not
measure the accuracy of these twelve 2026 predictions.

The private SQL backup of 2023–2025 sales is independently restore-verified in
`backups/actual-sales-sql-2023-2025-20261023/`. It retains all historical receipts,
including unclassified/null-customer/null-SI entries, while training continues to
exclude unclassified and dummy observations.
