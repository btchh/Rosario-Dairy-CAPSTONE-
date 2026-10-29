# Frontend handoff and completion status

Updated 2026-10-29 (Asia/Manila).

**Overall status: frontend implementation and combined Docker verification
complete locally.** Public production
hosting and forecast approval remain separate deployment/evidence decisions.

Frontend changes are on `beta/docker-handoff` in
`JayMarkRocero/Rosario-Dairy-Capstone`, based on upstream `7d59b2ef`.
Backend changes are on `beta/docker` in this repository. The latest implementation remains local until both beta branches are pushed. Both source trees must be shared together
when building from source; the complete Docker archive carries both app images.

## Current completion checklist

| Requirement | Local implementation status |
| --- | --- |
| Registration length limits and Unicode handling | Complete; retained |
| Selected dates on all six report previews and PDF exports | Complete; retained |
| Historical receipt labels and preserved source values | Complete; retained |
| Mutation cache invalidation and report-refresh cooldown handling | Complete; retained |
| Neutral adjacent past/current display bridge | Complete; preserves values, gaps and provenance |
| Historical dashboard forecast dates | Complete; retained |
| Per-customer lifetime total (`total_spent`) | Complete in API, table, profile and mobile card |
| 52-week/12-month Seasonal outlook | Complete; explicit control, centered history retained |
| Latest outlook, origin/horizon and original issued comparisons | Complete; optional separate comparison series |
| Component models, freshness, approval and baseline comparison | Complete; separate primary monthly publication evidence |
| Current-year actuals/estimates, missing months and actual floor | Complete; values come from the backend |
| Node browser-storage test isolation | Complete; independent session storage per test |
| Dependency review and CI/staging smoke workflow | Code complete; current audit reports zero vulnerabilities |
| Combined app browser verification | Complete; admin/staff, sale/cancel, stock, totals, preview/PDF and mobile checks |
| Complete app export | Both application images are exported by `docker/export-handoff.sh` |
| Public HTTPS hosting, SMTP and production database roles | Environment configuration required before public deployment |

Verification on 2026-10-29: all **397 tests pass** on
native Node 26 and in the Node 24 Docker build, without the earlier storage flag.
Strict TypeScript checking passes with the declared TypeScript 7.0.2 dependency.
The production Vite build and final frontend runtime image completed in Docker.
Browser checks passed for both roles, checkout, stock deduction/restoration,
customer totals and void exclusion, report preview/PDF, all nine forecast views,
seasonal controls and mobile layout. Browser forecast graphs used explicit
review fixtures; this verifies presentation rather than fitted-model accuracy.
All business workflow checks used the real API in a disposable Docker database. `npm audit` reports zero known
vulnerabilities after compatible Vite/router/transitive updates. The lockfile
matches the manifest; the outdated pnpm Vite override was removed.

## Customer lifetime sales total

**Implemented in the backend and local frontend.** Customer summaries include
the read-only `total_spent` field, a decimal string with two fractional digits
(for example, `"1250.50"`). This is an additive response field on
`GET /sales/customers/`, `GET /sales/customers/{id}/`, and customer create/update
responses. Existing summary fields and receipt/history responses remain compatible.

The amount sums each linked, non-voided transaction's saved `total_amount` across
the customer's entire history. It includes direct checkout, order-linked,
manually encoded and imported receipts once each, using final invoice amounts
after recorded discounts. It is independent of purchase-history pagination.
Customers without qualifying sales return `"0.00"`; inactive customers retain
their totals. Staff totals follow the same visibility rules as their transaction
count and history: hidden, inactive-category and mixed restricted receipts are
excluded in full. Admins receive all linked non-voided receipts.

All payment statuses count, including pending and unknown, so this is recorded
sales value rather than verified cash collected. Unassigned transactions do not
belong to a customer's total, and original imported source amounts are unchanged.

The frontend maps the decimal string to `totalSpent` and displays **Total
purchases** in the admin table/profile and mobile card. Formatting preserves
large values without rounding through JavaScript Number. An older API that
omits the field displays Unavailable, preserving the distinction from zero.
Existing mutation cache invalidation refreshes customer summaries after sales
and cancellations. Receipt pages are never used to calculate the total.

No database migration is needed. Restart backend processes after deployment.
For a warm shared cache, retire older business API payloads after restarting:

```bash
python manage.py shell -c "from config.data_cache import invalidate_data_cache; invalidate_data_cache()"
```

## Backend input validation update

Validation failures below return HTTP 400; show the backend's error message and
retain the form so the user can correct it.

- Submit positive integer IDs or integer strings for writable customer, category,
  product, ingredient and supplier references. Decimal IDs such as `1.9` are
  rejected instead of selecting record 1. Optional nullable fields remain nullable.
- Submit UUID strings for sale `request_id` values. Booleans and numeric UUID
  representations are rejected. Existing valid UUID retry/replay behavior remains.
- Submit JSON objects for catalog create/update requests; bare numbers and
  booleans now return JSON validation errors.
- `GET /sales/reports/best-sellers/` accepts an integer `limit` from 1 to 500,
  defaulting to 10. Values outside that range return `{"error":"..."}`.
- Stock-adjustment `reason` and stock-count `notes` accept strings or null,
  including blank strings; arrays, objects, booleans and numbers return 400.

Error bodies retain the existing shapes: an `error`/`detail` message, a field-to-
messages map, or a non-field error list. The frontend should support these forms.

## Backend cache consistency update

No frontend changes are required for this update. Failed shared-cache
invalidation now retains a database marker that prevents older sales, inventory,
report and runtime-settings payloads from being reused. Pending invalidations
recover automatically after the cache returns. Endpoint URLs, successful
response fields and existing validation error shapes remain compatible.

Backend deployment requires `python manage.py migrate` to apply
`config.0001_initial`, followed by restarting all backend workers. This migration
adds cache metadata only. Ordinary cached API lists perform two small indexed
database checks for freshness, in addition to existing staff visibility checks.

## Completed upstream: dashboard forecast historical dates

The new Dates control sends its To date as `as_of` to
`GET /api/forecasting/graph/` and uses a separate cache key for that extraction
date. Requests retain `scope=real` and reject invalid/future dates in the
Asia/Manila business timezone. Graph dates support years 0003 through 9997;
the existing report date contract below is separate.

The frontend keeps whole returned buckets overlapping the selected range,
without prorating amounts or changing nulls. The From date filters the returned
centered window; it does not retrieve arbitrary earlier history. If the range
starts before that window, the chart explains that those earlier periods were
not retrieved. Historical `refresh_required` results retain recorded sales and
available original issues and explain that Refresh forecast updates the current
window only. These date controls do not implement the longer seasonal outlook
or the pending model/readiness presentation.

## Forecast response and full seasonal outlook

**Implemented in the backend and local frontend.**

Graph engine `centered-beta-sarima-v6` and provisional engine
`actual-provisional-sarima-v4` expose the selected model through the response
metadata. Use the returned model labels and availability states for Regular,
Feeding and Combined; some components can use provisional models or be unavailable.

The endpoint, authentication, Staff/Admin permissions, centered seven-point
weekly/monthly windows and five-point yearly window remain compatible. The
backend adds `seasonal_outlook` to `GET /api/forecasting/graph/`:

| Field | Meaning |
| --- | --- |
| `seasonal_outlook.period`, `as_of` | Same requested weekly/monthly period and extraction date |
| `seasonal_outlook.range` | `start_date`, `end_date`, `periods`; 52 weeks or 12 months, including the current bucket |
| `seasonal_outlook.views[view].points` | Same point fields as the centered graph; one saved component origin across the outlook |
| `seasonal_outlook.views[view].readiness` | Availability, source freshness and false production approval, independently of the centered window |

Yearly returns `seasonal_outlook=null`; its annual nowcast semantics are unchanged.
The first four outlook predictions match the centered current/future predictions.
Longer display points are saved projections, not additional records of
forecasts actually issued in the past. GET replays their saved values. Combined
requires both component predictions; unavailable feeding never becomes zero.

Add an explicit **Seasonal outlook** display using this longer path. Keep the
current centered display available and keep Regular, Feeding and Combined
separate. Render the returned amounts directly and preserve nulls. A full-year
window makes recurring variation visible when the fitted model supports it;
this is not a requirement for large peaks or a smooth curve.

New per-point/current-view `model_details` fields include `display_label`,
`model_basis`, `primary_model_id`, `projection_steps`, `projection_step_unit`,
`missing_observation_periods_before_current`, `source_freshness`,
`primary_backtest` and `baseline_comparison`. Annual components instead expose
`projection_step_start` and `projection_step_end` for their monthly span.
Combined retains the separate `components` map. `projection_steps` counts from
the last fitted bucket; existing `horizon` remains extraction-relative. For
example, current regular weekly history can be 44 model steps old even while
`horizon=1`.

Top-level and per-view `readiness.source_freshness.status` is `current`,
`stale_data` or `unavailable`, with per-component last-observed and expected
closed periods. Preserve legacy `status=ready` as saved availability, and show
stale source history separately. `primary_backtest` identifies rolling
one-period evidence; do not display it as the accuracy of a 52-week path.
`baseline_comparison.result=worse` must remain visible when applicable.

## Data freshness and cache alignment

**Implemented locally.** Existing forecast-refresh and business-mutation
invalidation includes graph, report and PDF cache groups. Source-freshness,
approval and annual-null explanations are displayed using the contract below.

Read available history dates, source freshness and approval from the server.
Refreshing saved forecasts does not make stale observations current. Keep
unavailable annual totals and missing actuals null, and show the returned
availability message.

After a successful refresh or dataset reset, invalidate client graph/report/PDF
caches and fetch the current backend response. Do not retain old generated
results or hardcode history dates, model scores or approval states.

## Change request: one latest outlook and separate issued comparisons

**Implemented in the backend and local frontend.** Graph v6 preserves the
endpoint, permissions, nine views, field types and legacy statuses. Current and
future points use one latest outlook; older issued forecasts remain separate
comparisons. The current annual point is a disclosed nowcast.

Every graph point now adds these fields:

| Field | Meaning |
| --- | --- |
| `forecast_origin` | Exclusive training cutoff of the displayed prediction, not the first invoice date |
| `horizon`, `horizon_unit` | Weekly/monthly steps include the current bucket as 1; annual `year_offset` is 0 for the current year and 1 for next year |
| `outlook_basis` | Current/future: `latest_closed_period_forecast`, `single_origin_monthly_forecast_sum`, or `completed_month_actuals_plus_remaining_monthly_forecast`; null for past/demo points |
| `issued_prediction` | Separately retained pre-target issue when one exists; null means no issue, not zero |
| `issued_provenance` | Original `prediction_id`, `run_id`, `engine_version`, `issued_at`, `run_as_of`, `forecast_origin`, `cutoff_basis`, `source_revision`, `revision_kind`, `horizon`, `horizon_unit` |
| `is_nowcast` | True only for the real current-year hybrid |
| `annual_outlook` | Current-year component calculation, or a Combined `components` map; null elsewhere |

`forecast_origin`, `horizon`, `horizon_unit` and `is_nowcast` are always present;
the other added values can be null. Legacy `prediction_basis=forecast` is
retained for current/future points. Original issued comparisons identify their
own actual issuer and never inherit reconstructed `model_details`.
`cutoff_basis=issuing_run_calendar` explicitly identifies legacy origin
inference; `revision_kind=graph_input_revision` distinguishes that stored input
fingerprint from a raw sales snapshot revision.

The annual component contains `completed_months`, `missing_completed_months`,
`invalid_completed_months`, `invalid_current_month_actual`, `observed_revenue`,
`forecast_months`, `forecast_revenue`, `current_month`, `current_month_actual`,
`current_month_model_prediction`, `current_month_estimate`,
`current_month_floor_applied`, and `prediction`. The sum uses completed actual
months plus the current month estimate (at least its recorded amount) plus the
remaining monthly forecasts. Unknown completed months withhold the annual
total. It is not a January-issued twelve-month forecast; model component monthly
test metrics are not its annual accuracy. GET can update current-month actual
conditioning using saved fitted parameters; no model fitting occurs on reads.

Frontend implementation requested:

1. Extend graph types and preserve these fields. Label the current/future path
   **Latest outlook**, with origin and horizon in tooltips. Older issues belong
   in an explicitly labeled comparison, not as replacements for current/future
   values. Historical issues/tests can still differ at the outlook boundary.
2. Label the annual current point **Current-year outlook (actuals + estimates)**
   and explain its calculation, missing months and any actual floor. Keep actual
   partial totals distinct from full-period estimates. A forecast origin is a
   cutoff, not the amount's target date.
3. Use each point's `is_current` and `end_date` for current-period membership.
   October 26–November 1 is the current week on October 28. Bucket start dates
   earlier than today do not make the current bucket a completed historical one.
4. The upstream green `ReferenceLine` connector runs from the last past point
   into the current point. Use a neutral boundary connector or explicitly label
   it as a display bridge; its past endpoint is not a future prediction. Preserve
   true null gaps and all numeric values. Do not extend the latest-origin model
   backward through historical actuals to create a continuous-looking line.
5. Replace generic “SARIMA estimates” wording where selected components are
   AR(1)/local-level. Use the returned readiness/model details and approval state.
6. Test all nine views, zero-valued issues, unavailable components, missing closed
   months, January/December annual boundaries, same-origin current/future points,
   immutable issue comparison, horizon labels, and the neutral connector. Do not
   test for an artificially seasonal/nonflat future curve.

## Change request: distinguish saved projections from validated forecasts

**Implemented in the backend and local frontend.** `GET /api/forecasting/graph/`
retains existing fields, permissions and legacy `status` values. Graph v6's
current-outlook fields are specified above.
It now adds diagnostics without fitting models during requests:

- Top-level and per-view `readiness.status`: `available`, `partially_available`
  or `unavailable`, based on actual prediction endpoints. A saved graph can
  retain legacy `status=ready` while prediction availability is `unavailable`.
- `readiness.production_approved` is always false for this projection graph;
  top-level `readiness.publication_status` is `research_only`. Read strict
  monthly approval and missing evidence from the existing
  `/api/forecasting/models/` endpoint, also linked by
  `readiness.primary_monthly_status_url`.
- Each view adds `model_details` for its current bucket. Every point also adds
  `model_details` for that point's own model: `name`, `family`, `order`,
  `seasonal_order`, `seasonal_terms_enabled`, `forecast_origin`,
  `training_start`, `trained_through`, `training_period`, `observed_periods`,
  `missing_periods`, `selection_basis`, `candidate_models`, `validation`,
  `test`, `evaluation_period`, and `production_approved`.
  Training dates identify the starts of the training buckets: with
  `training_period=monthly`, `trained_through=2026-09-01` means the completed
  September bucket, not that invoices stop on September 1. Format by period.
- Combined model details contain `name=sum_of_component_predictions` and the
  separate Regular/Feeding `components`; do not label their sum as one model.
- Validation/test summaries expose `wape_percent`, `periods` and `basis` when
  saved evidence exists. They can be null, notably when the primary monthly
  artifact is reused; primary model evaluation remains in `/models/`.
- Original issued values have `model_details=null`. Do not describe their
  issuing model using the currently reconstructed historical model.

Use these fields to distinguish graph availability, model selection and
production approval. An AR(1) or local-level estimate is not evidence of annual
seasonality. Flat estimates can be mathematically correct; do not interpolate
or alter values to make a curve. Show training history and the number of
evaluation samples when communicating confidence. A monthly component test
does not validate a yearly forecast or the Combined sum.

For Yearly, show each point's `forecast_origin`: completed past years retain
pre-year information, while the current-year hybrid and future years share the
latest closed-month model origin. Identify the current-year actual conditioning
explicitly. Keep prediction-basis and partial-period explanations; adapt the
completed connector implementation below to the neutral treatment above.

## Browser storage and prediction-boundary regression coverage

`tests/storage.cjs` gives each test an independent session-storage map. The POS
and API harnesses reset it without changing production duplicate-sale guards.
The ordinary suite now passes on both Node 24 and 26. No runtime storage flag
is required.

The chart's adjacent prediction connector is neutral and explicitly described
as a display bridge. It adds no data point, tooltip value, legend item or
forecast evaluation. Existing calendar, zero/null, leap-year, transition and
immutability tests remain in place, with new tests for all nine period/view
combinations, seasonal horizons and optional original issued comparisons.

## Completed upstream: registration field length limits

Implemented in frontend `d4d35f44`, including code-point counting, Python-style
username whitespace handling before NFKC, and preserved password spaces.
The matching backend requirements below remain authoritative.

The backend now enforces these limits on admin-only `POST /accounts/register/`:

| Request field | Accepted length |
| --- | --- |
| `username` | Required; maximum 50 characters after trimming and Django NFKC normalization. |
| `first_name` (fname) | Maximum 50 characters; remains optional. |
| `last_name` (lname) | Maximum 50 characters; remains optional. |
| `password` | Required; 8–25 characters inclusive, with existing password-strength checks. |

Apply matching limits to the registration inputs and submit validation, and
show the limits beside the fields. Use the existing API keys `first_name` and
`last_name`. Count Unicode code points (`Array.from(value).length`), and apply
`username.trim().normalize('NFKC')` when validating the username's length. Native
HTML `minLength`/`maxLength` count UTF-16 units; ensure they do not reject valid
Unicode values within the backend limits. Backend validation remains authoritative.

Preserve password whitespace; never trim or silently truncate a password.
Out-of-range values return HTTP 400 with the existing `{"error": "message"}`
shape; display the server message. Successful registration still returns HTTP
201 with `{"message": "User registered successfully"}`. Registration remains
admin-only; these validation changes require no database migration and apply
to registration. Test 50/51-character text boundaries, 7/8/25/26-character
passwords, normalized Unicode usernames, and rejection without creating a user.

## Completed upstream: apply custom dates to inventory and forecast reports

Implemented in frontend `d4d35f44`: all six previews and PDF exports receive the
selected dates, with separate current inventory-health labels and full-period
forecast explanations. The earlier frontend dropped dates for `inventory` and
`sarima_forecast`; the matching contract and regression requirements follow.

### Request contract

Use the same inclusive range for previews and PDF downloads:

```http
GET /api/reports/preview/?type=inventory&start_date=2026-10-01&end_date=2026-10-20
GET /api/reports/export-pdf/?type=inventory&start_date=2026-10-01&end_date=2026-10-20
GET /api/reports/preview/?type=sarima_forecast&start_date=2026-10-01&end_date=2026-10-20
GET /api/reports/export-pdf/?type=sarima_forecast&start_date=2026-10-01&end_date=2026-10-20
```

Both dates are required when either is supplied. Use ISO `YYYY-MM-DD`, year 0002
or later, `start_date <= end_date`, and no future dates in the business timezone
(Asia/Manila). Omitting both dates preserves the existing report defaults.
Forecast access still requires an admin; the date range does not grant staff
access to global forecasts.

Inventory response additions under `data`:

- `start_date`, `end_date`, `is_custom_range` describe the selected movement period.
- `ledger.start_date` and `ledger.end_date` are its inclusive boundaries;
  `ledger.date` is retained and equals the end date.
- Ledger rows contain reconstructed opening balances, receipts, outlet/customer
  deductions, signed adjustments, and closing balances for that period. Physical
  movements use their recorded dates; backdated encoded invoices use their entry
  dates. Reconstruction uses retained movements and the current product catalog.
- `as_of`, `items`, low-stock counts, expiry counts, and `status_counts` remain
  **current inventory health**, not a historical FEFO snapshot. Label them with
  `as_of`; never present them as stock health at the custom end date. The PDF
  separates the selected-period ledger from its dated current-health section.

Forecast response additions under `data` when dates are supplied:

- `start_date`, `end_date`, `is_custom_range: true`, and `range_note`.
- `forecast` contains available approved predictions whose periods overlap the
  selection. Preserve each prediction's actual `date`, `end_date`, and full-period
  amount. Monthly predictions are not prorated to partial dates.
- The range filters the existing served forecast; it does not retrieve arbitrary
  archived models, fabricate historical predictions, or trigger fitting.
- When an otherwise ready forecast falls outside the selection, `forecast` is
  empty, `status` is `no_forecasts_in_range`, and the summary prediction amount is
  absent. Existing unavailable statuses such as `stale_data` remain unavailable.
  Display `range_note`, including the explicit no-approved-forecast message.

### Frontend implementation requested

1. In `src/features/reports/utils/reportDateRange.ts`, include `inventory` and
   `sarima_forecast` in date-range support and validation.
2. In `AdminReports.tsx`, forward the selected range to **all six** preview hooks
   and PDF downloads. Remove the exceptions that discard it for these two cards.
3. Show the server-returned date range on both cards. Inventory descriptions
   should identify stock movements; label its health figures as current with
   `as_of`. Forecast descriptions should identify available predictions for the
   selected dates and display `range_note` without implying a new fit was made.
4. Update `ReportDateRangeControl.tsx`: the range applies to all reports. Replace
   help text claiming inventory always uses today's ledger and forecasts ignore
   the selection. Explain the inventory-health and full-period forecast semantics.
5. Extend the inventory, inventory-ledger, and forecast report types with the
   response fields above.
6. Replace tests that expect these types to reject or discard custom dates. Verify
   all six previews and downloads send identical bounds; verify changing either
   bound, same-day ranges, cache invalidation, reset, validation failures, empty
   forecasts, and the distinction between period movements and current health.

Keep the existing range-aware cache keys, dated PDF filenames, and reset behavior.
Verify they also work for inventory and forecast reports after enabling their
date ranges. Run `npm test` and `npm run build` and report the results.

## Completed upstream: historical receipt amount labels

Implemented in frontend `d4d35f44`; the source-value explanation and preservation
requirements below are retained for regression review.

In `src/features/sales/utils/receiptDetails.ts`, imported records use
`source_line_total` for their displayed line amounts. Those source amounts can
remain gross when a discount was recorded only on the invoice header. The
`Net amount` heading in `TransactionDetails.tsx` therefore overstates what is
known about each imported line.

Use **Recorded line amount** for imported receipts and retain **Net amount** for
ordinary local sales. Explain that historical line amounts are recorded source
values and may precede an invoice-level discount; line discount allocations
were not recorded. Preserve the server's subtotal, discount and invoice total.
Do not allocate header discounts across lines, change historical values, or
treat missing values as zero. The existing transaction API already supplies
`source_reference`, `source_line_total`, `subtotal`, `discount_amount` and
`total_amount`; no backend contract change is needed.

Cover imported invoices with header discounts, missing source amounts, grouped
lines, and ordinary discounted checkout receipts in the receipt mapping/UI
tests. Verify invoice totals remain exactly as recorded.

## Completed upstream: refresh after successful sales

Frontend `d4d35f44` removes automatic explicit report refresh after checkout and
order writes while retaining mutation invalidation. Manual refreshes coalesce
and respect `Retry-After`. The earlier checkout implementation awaited
`reportsService.refreshAfterMutation()` after every successful sale.
The explicit refresh endpoint intentionally permits one refresh per minute
per account and also enforces a global cooldown across accounts/processes.
Consecutive successful sales can consequently produce a report warning.

Prefer existing mutation-driven client cache invalidation and refetching for
automatic updates. The backend invalidates business/report cache versions after
committed writes; preview requests then read the new version. Keep explicit
`POST /api/reports/refresh/` for the manual refresh action. If an automatic
refresh is retained, coalesce concurrent work, respect `Retry-After`, and treat
its 429 as a refresh cooldown rather than a failed sale. Client report/PDF
caches must still invalidate after **every** committed mutation, even during
the cooldown. A failed sale must not emit a successful-save message, and a
refresh failure must not resubmit checkout.

Test two rapid successful checkouts, concurrent mutations, fresh preview/PDF
data after each sale, and manual refresh throttling. Keep the backend throttle
and global lock; increasing their rates is not the fix.

## Audit follow-up: forecast availability and evidence

Display `status`, `status_message`, `limitations`, `warnings` and available
evaluation counts returned by the forecast endpoints. A `stale_data` report is
unavailable even when centered graph data exists. An empty evaluation is **not
yet evaluated**; one or two completed periods are limited evidence, not current
validated predictive accuracy. Display evaluation metrics with their returned
basis; targets must not be presented as achieved live accuracy. Preserve the existing
date-range availability rules above and do not trigger fitting from a preview.

## Audit follow-up: deployment origin and dependency review

Set the production build's API origin to the provisioned HTTPS backend and
verify login, both roles and complete sale/report workflows in staging. The
local development origin is not a production deployment configuration.

A read-only `npm audit --json` on 2026-10-27 reports **10 affected packages**
(9 high, 1 moderate) in this checkout's lockfile: `baseline-browser-mapping`,
`browserslist`, `follow-redirects`, `nanoid`, `postcss`, `react-router`,
`react-router-dom`, `source-map-js`, `tar`, and `vite`. These are registry advisory
ratings, not proof of browser exploitability. This updates the pasted audit's
three-package result; the lockfile was not changed during backend remediation.

Review application, build and development-server reachability; update manifest
and lockfile together to fixed releases, or record a specific justified
exception with advisory, affected path and scope. Re-run the current advisory
audit, frontend tests, strict TypeScript checks and production build after the
updates. Do not use a forced dependency upgrade without checking compatibility.
