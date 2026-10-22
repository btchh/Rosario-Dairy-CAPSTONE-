# Remaining frontend integration

Updated 2026-10-22 (Asia/Manila). Checked against JayMark frontend commit
`393383a3`. This handoff contains pending integration work only.

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
backtest; weekly covers the weeks touching that window; yearly shows all recorded
years so its graph is not reduced to a single annual point. Historical comparisons can display while the live status is `stale_data`;
do not hide the entire historical graph because a current forecast is withheld.
If source data changes, old prediction lines and scores are withheld until an
aligned model result exists.

All three time-period selectors remain available even when a prediction line is
unavailable. Weekly shows actual recorded sales; it does not split monthly
forecasts into invented weekly predictions. Yearly includes a regular prediction
sum only when all twelve aligned monthly backtests exist for that year. Incomplete
years have a null predicted value. Weekly/yearly `metrics` remain null because
monthly WAPE is not accuracy at those horizons. `validated_forecast_periods`
currently contains only monthly; it does not restrict which history views can be
selected. Approved live points remain gated and monthly-only.

Only regular sales currently has a real-data monthly SARIMA backtest (10.6433% WAPE).
Feeding has no validated model, so its predictions and error are unavailable.
Combined predictions are also withheld until both components are available;
never copy regular predictions into Combined or substitute zero for feeding.
No dummy sales are used. The retired synthetic endpoint returns HTTP 410.

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

Model details remain available from `/api/forecasting/models/`; the feeding
history audit remains available from `/api/forecasting/feeding/`. Neither needs
an additional panel for the requested three-tab line graph.

## Reference

- [Forecast API and publication rules](forecasting/README.md)
- [Milk-feeding evidence and limitations](forecasting/FEEDING_RESEARCH.md)
