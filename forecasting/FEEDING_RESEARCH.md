# Actual-invoice feeding investigation — 2026-10-22

Forecasting and evaluation use actual invoices only, per the user's latest
instruction. Dummy sales and their previous scores are excluded. Existing
synthetic experiment rows and private exports remain archival records, with no
training, serving or export path enabled. The disabled API returns HTTP 410.

The user elected to use recorded invoices without external delivery schedules
or contract quantities. No future delivery plans, quantities or program start
months are inferred. The target is recorded revenue, not delivery demand.

## Available evidence

The actual dairy snapshot contains 38 feeding invoices, from November 26, 2024
through December 14, 2025. Ten months have invoices across a 14-month span.
February, May, June and July 2025 have no feeding invoices. These gaps are not
verified absence of demand. There are nine subsequent completed months without
another feeding record at the October 1, 2026 exclusive cutoff.

Descriptive source groups: DSWD (12 invoices), DepEd (5), Las Piñas (10),
Rizal (6), unspecified feeding program (5). Generic/week labels remain
unspecified. Grouping preserves aliases and never merges database customers.

## Readiness and next evidence

A 14-month billing history with gaps does not establish a dependable annual
feeding cycle. The API flags fewer than 24 calendar months as limited history;
that threshold is an engineering review policy, not automatic model approval.

Earlier exploratory SARIMA probes on actual 2025 validation data failed to
improve the current research selection. Those probes padded the periods before
feeding records with zeros; that is an unsupported coverage assumption, so
neither their scores nor their apparent calendar length establishes readiness.
No extra model configurations were added to the production candidate pool.

Review billing coverage and internal gaps before treating them as observed
zeros. Do not pad the pre-program period to manufacture training history.
New actual invoices supply future evidence. Feeding requires separate prospective
forecast evaluation before approval; no current feeding forecast is approved.
Rough feeding projections now appear separately in the chart API’s
`provisional_forecast` arrays for weekly, monthly and yearly views. They use
recorded invoices only, leave gaps unknown, and currently disable seasonal terms
because the observed history is short. These are statistical revenue projections,
not inferred delivery plans. See [forecasting documentation](README.md).

The actual-history API refreshes as invoices arrive. The regular SARIMA model,
actual source policy and publication gates are unchanged; the maintenance worker
now also refreshes the separate rough projections.
Regular historical WAPE remains 10.6433%, with stale-data publication status.

`investigate_feeding_sales --output <folder>` exports only actual monthly evidence
and gap/readiness diagnostics. Private backup and actual exports are under
`backups/feeding-research-20261022/actual-results/`.
