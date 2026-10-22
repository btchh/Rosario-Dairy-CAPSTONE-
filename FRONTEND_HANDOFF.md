# Remaining frontend integration

Updated 2026-10-23 (Asia/Manila). Checked against JayMark frontend commit
`3d5e913f`. This handoff contains pending integration work only.

## Three views in the existing forecast line graph

Keep one forecast card with three tabs, in this order:

1. **Combined** — regular plus milk-feeding recorded revenue.
2. **Regular sales** — regular dairy revenue and its SARIMA predictions.
3. **Milk feeding** — feeding revenue and its own predictions when available.

Keep **Weekly / Monthly / Yearly** as a separate selector alongside these sales
tabs. All nine combinations use the same line graph. No additional audit cards,
program tables or separate feeding dashboard are required.

New admin-only endpoint, through the existing authenticated API client:

```http
GET /api/forecasting/chart/?period=weekly
GET /api/forecasting/chart/?period=monthly
GET /api/forecasting/chart/?period=yearly
```

The direct response includes `available_views`, `default_view: combined`,
`period`, `available_periods: [weekly, monthly, yearly]`, `source_revision`,
`as_of_exclusive`, `default_range` and `views`. Use `views[selectedTab]` to populate
the graph; switching sales tabs needs no new fetch. Changing the time period
fetches the corresponding response. Include `period` in frontend query/cache keys.
The default period is monthly.

| Field in each view | Graph behavior |
| --- | --- |
| `label` | Tab/series title |
| `history` | Period points: `date`, `end_date`, `actual`, `predicted`, `invoice_count`, `record_status`, `is_partial_period` |
| `metrics.wape_percent` | Historical error for this view only; metrics is null without valid predictions |
| `historical_prediction_status` | Whether a retained comparison line is available |
| `forecast` | Approved live points only; empty means withheld |
| `provisional_forecast` | Rough future points for the selected sales view and period |
| `provisional_status`, `provisional_metadata` | Rough prediction availability, model evidence, stale history and limitations |
| `status`, `status_message` | Explain the view's current prediction availability |

Render `actual` as the solid recorded-sales line and `predicted` as the dashed
historical comparison line. Monthly predictions are SARIMA backtests. For yearly
rows label the dashed series **Sum of monthly SARIMA predictions**, using
`prediction_basis: sum_of_12_monthly_rolling_predictions`; it is not a prediction
issued before the year began. Keep null values as gaps (`connectNulls={false}`); never
replace them with zero. If predictions are unavailable, retain the recorded line
and show a short availability message. A recorded zero from a real invoice is
separate from a month with no invoices (`actual: null`).

For each selected time period, all three views share the same calendar.
Weekly buckets run Monday–Sunday, monthly buckets are calendar months, and yearly
buckets are calendar years. Weekly buckets can span two years; use `date` and
`end_date` for labels. Period ends are clipped to the source cutoff; label
`is_partial_period: true` as a partial week/year rather than a full-period total.
Actual totals are grouped directly from invoice dates. Combined recorded values sum
classified regular and feeding invoices; unclassified sales are excluded. A
month with only one segment's invoices contains that recorded amount, not an
attestation that the other segment had zero demand. Do not sum or average
component WAPE to fabricate a combined score.

Use `default_range` for the initial historical comparison window when supplied;
other retained periods remain in `history`. Monthly defaults to the 2025
backtest; weekly defaults to its separate 13-week test window; yearly shows all
recorded years so its graph is not reduced to a single annual point. Historical
comparisons can display while the live status is `stale_data`;
do not hide the entire historical graph because a current forecast is withheld.
If source data changes, old prediction lines and scores are withheld until an
aligned model result exists.

All three time-period selectors remain available even when a prediction line is
unavailable. Weekly uses a separately fitted SARIMA research model when an aligned saved
backtest exists; it never splits monthly forecasts into weekly values. Yearly includes a regular prediction
sum only when all twelve aligned monthly backtests exist for that year. Incomplete
years have a null predicted value. Weekly `metrics` describe the saved weekly backtest. Yearly `metrics` remain
null because sums of monthly predictions are not independent annual evaluation. `validated_forecast_periods`
currently contains only monthly; it does not restrict which history views can be
selected. Approved live points remain gated and monthly-only.

Regular monthly SARIMA retains its 10.6433% historical WAPE. Separate weekly
SARIMA research now has 26.8135% WAPE over 13 later weeks, versus 20.2992% for its
validation-selected baseline. Label weekly scores as retrospective research;
they do not pass a weekly production gate or establish the monthly 15% target.
Feeding has no approved model. Rough predictions for all three sales views are
available separately in `provisional_forecast`, as described below. Approved
Combined predictions remain withheld until both approved components are available.
Never copy Regular into Combined or substitute zero for an unavailable component.
No dummy sales are used. The retired synthetic endpoint returns HTTP 410.

## Rough future predictions in all nine combinations

Keep all sales tabs and Weekly / Monthly / Yearly buttons visible. Read
`views[selectedTab].provisional_forecast` for **13 future complete weeks, 12 months,
or 2 calendar years**. Top-level `provisional_targets` gives the dates. Each point
has `date`, `predicted`, `lower_bound`, `upper_bound`, `is_provisional: true` and
`production_approved: false`. Combined adds matching component predictions;
yearly values sum twelve monthly predictions from a single forecast origin.

Append these dates to the recorded graph calendar with `actual: null` and label
the new dashed series **Rough SARIMA prediction**. Extend the displayed range to
include them: `default_range` describes the historical comparison only. Keep
unknown recorded periods as gaps; do not fabricate 2026 actuals or connect through
missing history. Optional shaded bounds must be labelled **Uncalibrated estimate
range**; summed component ranges are not a combined probability guarantee.

Use `provisional_status: provisional` to display this rough line even when the
legacy `status` is `stale_data`, `not_modelled` or `horizon_not_validated` and
approved `forecast` is empty. Display a short **Rough estimate — limited/stale
invoice history** caption from `provisional_metadata.warnings`. The regular and
feeding metadata includes `model`, `seasonal_terms_enabled`, `observed_periods`,
`missing_periods`, `stale_history` and `training_age_days` when available.
With `refresh_required`, `insufficient_data`, `fit_failed`, `component_unavailable`
or `invalid_artifact`, retain recorded history and an availability message.

Keep `metrics.wape_percent` attached to its existing historical comparison.
`provisional_metadata.metrics.wape` is a different single-origin observed-period
test, with `sample_count`, `rows` and `basis`; do not substitute it for the primary
monthly score or imply future accuracy. Provisional yearly/Combined metrics are
null. The primary regular monthly 10.6433% historical error does not describe the
rough regular model, feeding, or a twelve-month future path.

Short feeding history currently selects a nonseasonal local-level SARIMA special
case, so its future line can be flat. Do not infer seasonal events or contracts.
Nightly refresh is scheduled for **12:00 midnight, Asia/Manila**; eligible new or
corrected data is incorporated then or through the manual button below. Unchanged
component observations reuse saved state. `validated_forecast_periods` describes
legacy monthly research support and must not hide rough horizon controls.

## Manual refresh button

Add one admin **Refresh forecast** button beside the existing graph selectors.
It refreshes all sales views and horizons together. Keep the selected tab/period;
use a spinner and short inline status, with no extra dashboard panel.

```http
POST /api/forecasting/refresh/
Content-Type: application/json

{}
```

Use the authenticated API client and send no date, scope, model or force fields.
The backend returns **202 Accepted**, not a finished-training result. The payload
includes `job_id`, `status`, `stage`, `already_running`, `message` and `status_url`
(e.g. `/api/forecasting/refresh/12/`).

1. On page/card load, `GET /api/forecasting/refresh/` recovers the latest job;
   `status: idle` means no prior job. Resume polling if queued/running.
2. Disable the button while `queued` or `running`, show **Refreshing…**, and poll
   the returned `status_url` every **5 seconds**. These GET responses are uncached
   and never fit. Stop polling on unmount; the job continues independently.
3. On `succeeded`, stop polling and invalidate/refetch chart queries for all three
   periods plus visible forecast/model/report queries. Show **Forecast refreshed**.
   If every phase result has `created: false`, **Forecast already up to date** is
   appropriate: unchanged fits are reused.
4. On `failed`, stop polling, show the safe returned `message`, and enable retry.
   Refetch the graph for any completed updates; old valid versions are retained.

GET latest/detail responses include status/stage, trigger, timestamps, results,
error code/message and status URL. The ID-specific URL accepts GET polling, not
POST. Map stage names to user-facing text:

| Stage | Text |
| --- | --- |
| `queued` | Waiting to refresh |
| `refresh_forecast_patterns` | Checking sales |
| `refresh_forecast_models` | Updating monthly forecasts |
| `refresh_weekly_forecasts` | Updating weekly forecasts |
| `refresh_provisional_forecasts` | Updating forecast views |
| `refresh_projection_evidence` | Checking forecast results |
| `complete` | Forecast refreshed |

Repeated accepted requests share the active job (`already_running: true`). Manual
and midnight jobs cannot fit concurrently. **409** means maintenance is starting:
wait and check the latest-job endpoint. **429** means the one-start-per-minute
account limit was reached; respect `Retry-After` and continue polling an existing
job. Preserve normal **401/403** handling; unknown job IDs return **404**.

Tooltip: **Refresh using the latest completed weeks and months.** Unfinished
periods remain excluded: an older completed-period correction can retrain now,
while a current entry waits for its training period to close. Completion does
not guarantee better accuracy or production approval. Preserve provisional/stale
labels and do not add coverage-attestation controls to this button.

## Reconcile the existing forecast report summary

The existing `sarima_forecast` report preview and
`GET /api/forecasting/forecast/?period=monthly` remain regular-only compatibility
routes. They do not select the three graph tabs.

- Display the backend `status_message` and `quality_gate.reasons` when relevant.
- Their historical score is `metrics.retrospective_regular_monthly`; reviewed
  future scores are `metrics.prospective_regular_monthly`, not `metrics.combined`.
- The compatibility forecast/report route still supports monthly predictions
  only. Use the new chart endpoint's `available_periods` for the Weekly / Monthly /
  Yearly graph selector; do not use the compatibility route to hide these options.
- Publish a current value only with `status: ready` and a valid forecast point.
  The saved January 2026 research point is not a current approved forecast.
- The old total-sales baseline map, fixed-origin comparison and planning estimate
  do not match the current backend contract. Do not manufacture missing values.

Monthly model details remain at `/api/forecasting/models/`. Separate weekly
research details are at `/api/forecasting/weekly/`: model/source/cutoff,
`metrics`, `baseline_metrics`, `beats_baseline`, candidates, retained test rows,
and an explicitly non-live `research_forecast`. `forecast` remains empty and
`production_ready` is false. Weekly `as_of_exclusive` is Monday (current partial
week excluded); monthly/yearly chart snapshots exclude the current partial month.
A source change withholds old weekly comparisons until offline maintenance saves
an aligned revision. GET never fits. Backend setup is documented in the forecast
README; no migration or maintenance command belongs in the frontend flow.

The feeding
history audit remains available from `/api/forecasting/feeding/`. Neither needs
an additional panel for the requested three-tab line graph.

## Accuracy labels on the existing graph

Admin-only `GET /api/forecasting/evidence/` exposes prospective evidence for the
provisional chart models. If showing accuracy in the existing graph's tooltip,
match `period`, `view`, `horizon` and `engine_version`; do not combine horizons
or substitute the primary monthly research score. `scored_count: 0` or a null
`wape` means accuracy is not yet available, not zero error. Pending outcomes and
unreviewed/corrected periods do not count as scored. This endpoint is read-only
and does not refresh models. Keep the existing graph; no new panel or coverage
review controls are needed. Provisional labels remain required regardless of
scores; this evidence does not approve production forecasts.

## Reference

- [Forecast API and publication rules](forecasting/README.md)
- [Milk-feeding evidence and limitations](forecasting/FEEDING_RESEARCH.md)
