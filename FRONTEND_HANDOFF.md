# Frontend handoff and completion status

Updated 2026-10-30 (Asia/Manila).

**Overall status: partially satisfied.** The latest frontend implements most
requested UI work. Remaining changes and verification evidence are detailed in
[FRONTEND_HANDOFF_REVIEW.md](FRONTEND_HANDOFF_REVIEW.md). These displays do not
imply production forecast approval.

**Work boundary:** make backend changes in this repository. Communicate frontend
implementation requests through this document; do not edit the sibling frontend
repository during backend maintenance.

Reviewed frontend `origin/main` at
[`49c3cb42`](https://github.com/JayMarkRocero/Rosario-Dairy-Capstone/commit/49c3cb42)
on 2026-10-30, after pulling three upstream commits with a clean working tree.
This review did not modify frontend application code, tests, dependencies or
configuration.

## Client wording revision — 2026-10-30

The client needs a sales-planning screen, with short explanations of what is
available and what action is needed. The earlier request to expose extensive
model evidence, publication gates and provenance in the normal client interface
is withdrawn. **This section supersedes the detailed display instructions below
where they ask for research diagnostics on the client screen.** Keep those API
fields and their meaning for developer documentation, capstone evaluation and
internal verification; do not remove data, alter estimates or change approval
rules to simplify the display.

Requested frontend revision:

- Remove **Model evidence and limitations**, raw model orders/IDs, WAPE/baseline
  tables, training-step counts, source fingerprints, publication-gate codes and
  technical provenance from the ordinary client dashboard and report summary.
- Remove **Compare issued forecasts** from the ordinary client controls. It
  compares a previously saved prediction with the latest estimate for the same
  period; it is an internal evaluation feature. Keep original issued values
  separate and unchanged in the API and research evidence.
- Show the selected sales group, date range, **Recorded sales** and **Estimated
  sales**. For the longer display, use **Next 12 months** or **Next 52 weeks**
  instead of asking the client to interpret “Seasonal outlook.”
- Keep an honest short notice such as **For planning only** when estimates are
  not approved for operational use. Missing data stays unavailable, not zero.
  For the current annual point, **Year-end estimate** with “Recorded sales plus
  estimates for the remaining months” is enough; detailed calculation fields
  belong in documentation.
- Do not append `quality_gate.reasons`, `warnings`, `limitations`, model scores
  or raw status identifiers to the Sales Forecast Report card. The shared
  `forecastExplanation` currently displays backend `status_message` verbatim
  and appends “Quality checks”; replace that client-facing path with a short
  status-based explanation. Keep technical fields available internally.

Suggested report copy (preserve the underlying status and forecast data):

| Backend status | Client message |
| --- | --- |
| `stale_data` | A current forecast is unavailable because recent sales records are missing. |
| `research_only` | The forecast is still being checked and is not ready to use. |
| `refresh_required` | The forecast needs updating. |
| `training_required`, `pending_update`, insufficient-history states | There is not enough sales history to prepare a forecast yet. |
| `unsupported_horizon` | A forecast is not available for this period. |
| `no_forecasts_in_range` | No forecast is available for the selected dates. |
| `ready` | A forecast is available for the selected period. |
| Unrecognized status or request failure | Forecast information is currently unavailable. |

If helpful, show **Latest sales record: [date]** from `data_end`. Do not print a
list of missing model approvals, “demo data excluded,” or “12 prospective
reviewed months.” Those remain internal requirements; adding recent sales does
not automatically satisfy every approval check. Do not offer a button as a
guaranteed fix for missing observations or a pending review.

The downloadable forecast PDF already uses short availability messages in
`reporting/pdf.py`; the browser card should be similarly readable. The backend
`forecasting/serving.py` still supplies diagnostic `status_message` text for
compatibility. Frontend display selection can simplify it without changing the
response shape or hiding availability/approval facts.

Verification: inject the quoted stale-data response with all three quality
reasons and confirm the card shows only the plain explanation (and optional
last-record date). Check pending review, missing dates, zero forecasts, current
annual estimates, all sales views and loading/error states. Verify the graph
values and API metadata remain unchanged, and client controls contain no model
evidence panel or issued-comparison switch.

## Current completion checklist

| Requirement | Status in frontend `49c3cb42` |
| --- | --- |
| Registration length limits and Unicode handling | Complete |
| Selected dates on all six report previews and PDF exports | Complete |
| Historical receipt amount labels and preserved source values | Complete |
| Mutation cache invalidation and manual report-refresh handling | Complete; respects separate preview/PDF cooldowns |
| Adjacent past/current chart connector | Complete; neutral display bridge, genuine null gaps retained |
| Historical date controls on the dashboard forecast chart | Complete |
| Per-customer lifetime total (`total_spent`) | Partial; table/profile/mobile displays implemented, but an open profile retains an old total after the list refreshes |
| Server retry UUIDs for checkout/orders | Pending; existing unconfirmed-sale guard remains, but callers still send no UUID |
| Explicit 52-week/12-month Seasonal outlook display | Complete; covered for all three sales views |
| Latest outlook labels, origin/horizon tooltips and separate issued comparisons | Complete; zero-valued original issues remain separate |
| Forecast diagnostics for research/verification | Graph metadata available; strict monthly evidence remains available from the backend. Showing technical approval details to the ordinary client is no longer requested |
| Plain-language client forecasting and report summaries | Pending; simplify the technical panels, labels and report text according to the wording revision above |
| Current-year nowcast, missing months and actual-floor explanations | Complete; server calculations and nulls retained |
| Node 26 browser-storage test isolation | Pending; default suite still has seven POS failures; all 426 pass with the documented storage flag |
| Production origin and staging workflows | Pending deployment verification |
| Dependency review | Pending; current registry audit reports 9 high and 1 moderate affected packages |

Verification of `49c3cb42`: the production build and strict TypeScript checks
pass. Under Node 26.12.1, the default suite passes **419/426**, while the
`--no-experimental-webstorage` verification run passes **426/426**. A separate
in-memory probe reproduces the stale open customer profile; it ran outside the
frontend repository and is not part of those 426 upstream tests. No live sale,
customer edit, cancellation or stock write was made during this review.

## Customer lifetime sales total

**Backend implemented; frontend display implemented, open-profile refresh pending.** Customer summaries include
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

Implemented in `49c3cb42`: map the server's decimal string to
`totalSpent` and display **Total purchases** in the admin customer table,
profile and mobile card. Preserve decimal precision when formatting; do not
calculate totals from paginated receipt history. If an older API omits the
field, display Unavailable rather than zero. Refresh customer summaries after
sales and cancellations using the existing mutation cache invalidation.
Explain that the total includes pending-payment recorded sales. Mapping/display
tests cover zero, missing values, large amounts and the voided-receipt definition.
The backend remains responsible for the actual voided-receipt sum.

Remaining correction: `AdminCustomers` stores the selected profile as a separate
customer object. When the cached list refreshes, its table/mobile totals update,
but an already-open profile keeps the old total. Resolve the displayed profile
by customer ID against fresh list data, or refresh its detail record. Cover a
sale/cancellation refresh while the profile stays open.

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

## Load audit and checkout/order retry follow-up

The 2026-10-29 backend audit passed a 10-client, one-minute mixed read workload:
595 successful requests, overall p95 1.80 seconds. A 25-client unpaced burst
completed without HTTP errors but had p95 6.82 seconds. These are development
measurements, not production capacity guarantees. See `BACKEND_VALIDATION.md`
for workload details, endpoint tails, input checks and limitations.

Malformed inventory reactivation IDs return JSON 404. Zero, negative and
out-of-range `customer_id`/`handled_by` sales filters now return 400. Keep form
values and display the existing backend error shapes; successful response
contracts are unchanged. Honor `Retry-After` on 429 responses.

Checkout and order callers in frontend `49c3cb42` still do not send a request UUID.
Keep their existing unconfirmed-sale guard. As a follow-up, generate and retain
one UUID per logical submission, send it as `request_id` or `Idempotency-Key`,
and reuse it only with the same payload when retrying an ambiguous outcome.
The first successful submission returns 201; the same UUID/payload returns
200 with the original receipt/order. Changed payload with an already-used UUID
returns 409. Use a fresh UUID for a genuinely new sale. Do not automatically
retry an unkeyed timeout/503 as a new checkout or clear the history-check guard.

Cover concurrent double submission, timeout/remount recovery, matching body and
header keys, changed-payload conflicts, and stock deducted once. Update the
stale comment in `src/lib/saleSubmission.ts` that says the routes have no
idempotency support. This follow-up is compatible with existing callers;
backend UUID fields remain optional. No frontend source was changed in this audit.

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
window only. The separate Seasonal outlook control and graph model/readiness
presentation are implemented in `49c3cb42`; the client wording revision above
now requests a simpler presentation.

## Forecast response and full seasonal outlook

**Backend implemented; frontend display implemented in `49c3cb42`.** The contract
below is retained for regression checks.

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

**Graph/report presentation implemented in `49c3cb42`.** Forecast-refresh and
business-mutation invalidation includes graph, report and PDF cache groups.
Source-freshness, graph approval and annual-null explanations are displayed.
The open customer-profile refresh and plain-language forecasting revision are
separate remaining requests, documented above and in the review.

Read available history dates, source freshness and approval from the server.
Refreshing saved forecasts does not make stale observations current. Keep
unavailable annual totals and missing actuals null, and show the returned
availability message.

After a successful refresh or dataset reset, invalidate client graph/report/PDF
caches and fetch the current backend response. Do not retain old generated
results or hardcode history dates, model scores or approval states.

## Change request: one latest outlook and separate issued comparisons

**Backend implemented; frontend adopted in `49c3cb42`.** Graph v6 preserves the
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

**Graph diagnostics implemented in `49c3cb42`; simplify the ordinary client view
according to the wording revision above.** `GET /api/forecasting/graph/`
retains existing fields, permissions and legacy `status` values. Graph v6's
current-outlook fields are specified above.
It now adds diagnostics without fitting models during requests:

- Top-level and per-view `readiness.status`: `available`, `partially_available`
  or `unavailable`, based on actual prediction endpoints. A saved graph can
  retain legacy `status=ready` while prediction availability is `unavailable`.
- `readiness.production_approved` is always false for this projection graph;
  top-level `readiness.publication_status` is `research_only`. For internal
verification, read strict monthly approval and missing evidence from the existing
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

## Test follow-up: isolate browser storage under Node 26

Earlier frontend `de34e12c` built successfully and its complete suite passed
**378/378 tests** when run with
`node --no-experimental-webstorage --test tests/*.test.cjs`.
Under this host's default Node 26.12.1, **371 tests passed and seven POS UI tests
failed**, reproducing the issue previously found at `d4d35f44`. Native
`sessionStorage` retains an earlier test's
unconfirmed-sale entry because test setup resets only `localStorage` and caches.
The same POS test passes alone; running the preceding ambiguous-order test
first reproduces the failure. This is test isolation, not a new API regression.

Reset or mock `sessionStorage` in the frontend test setup, including pending-sale
state, and verify tests both individually and as a suite. Preserve the production
duplicate-sale guard. The runtime flag above is an isolated verification aid;
no frontend source or dependency files were modified by this backend review.

## Completed upstream: basic adjacent past/current forecast connector

**Neutral connector implemented in frontend `49c3cb42`.** The earlier green
reference implementation below is retained as historical context; the active
frontend correctly uses a neutral bridge with a display-only explanation.
Preserve this neutral treatment when changing the chart. The backend returns consecutive calendar
buckets and intentionally separates `past_prediction` from
`future_prediction`. The artificial break at the current-period boundary comes
from `src/features/dashboard/components/admin/ForecastChart.tsx`: three
independent Recharts lines have no shared endpoint. There is no missing backend
bucket to insert. Preserve the existing forecast API and all fitted values.

Remove this artificial connector gap for Weekly, Monthly and Yearly, across
Combined, Regular and Feeding. Preserve genuine gaps for unavailable values.
A vertical difference between adjacent predictions can remain: joining the
points must not flatten, smooth, refit or replace their amounts.

### Implementation

1. In `src/features/dashboard/utils/forecast.ts`, add a pure helper deriving a
   display-only segment from the bucket immediately before `is_current` to the
   current bucket. Use the previous `past_prediction` and current
   `future_prediction`; finite numeric zero is valid.
2. Require consecutive calendar buckets: previous `end_date + 1 UTC day` must
   equal current `date`. Reject malformed dates, missing endpoints, no current
   bucket, or a missing immediate predecessor. Do not search backwards or
   forwards for another known value.
3. Render that segment as a dashed green `ReferenceLine`, which is supported by
   the installed Recharts 2.15.2. Keep the existing three `Line` components and
   `connectNulls={false}`.

Suggested helper: add `ForecastGraphPoint` to the existing type import from
`../api/forecasting.service` in `utils/forecast.ts`:

```ts
type ForecastBoundarySegment = [
  { x: string; y: number },
  { x: string; y: number },
];

export function forecastBoundarySegment(
  points: readonly ForecastGraphPoint[],
): ForecastBoundarySegment | null {
  const index = points.findIndex(point => point.is_current);
  if (index <= 0) return null;
  const previous = points[index - 1], current = points[index];
  const from = previous.past_prediction, to = current.future_prediction;
  if (typeof from !== "number" || !Number.isFinite(from)
      || typeof to !== "number" || !Number.isFinite(to)) return null;
  const previousStart = new Date(previous.date + "T00:00:00Z");
  const end = new Date(previous.end_date + "T00:00:00Z");
  const start = new Date(current.date + "T00:00:00Z");
  if ([previousStart, end, start].some(day => !Number.isFinite(day.getTime()))) return null;
  if (previousStart.toISOString().slice(0, 10) !== previous.date
      || end.toISOString().slice(0, 10) !== previous.end_date
      || start.toISOString().slice(0, 10) !== current.date
      || previousStart.getTime() > end.getTime()
      || previousStart.getTime() >= start.getTime()
      || end.getTime() + 86_400_000 !== start.getTime()) return null;
  return [{ x: previous.date, y: from }, { x: current.date, y: to }];
}
```

Import the helper in `ForecastChart` and derive
`const boundary = forecastBoundarySegment(chartData)`. `ReferenceLine` is
already imported there. Before the existing lines, render:

```tsx
{boundary && <ReferenceLine
  className="forecast-boundary-connector"
  segment={boundary}
  stroke={C.green}
  strokeWidth={2.5}
  strokeDasharray="3 4"
  ifOverflow="discard"
  aria-hidden="true"
  pointerEvents="none"
/>}
```

This is a presentation overlay. Do not copy a past value into
`future_prediction`, add synthetic forecast points, interpolate missing data,
or join actual sales to a prediction. Preserve raw response/cache objects,
dates, nulls, prediction bases, partial-period flags and backend evaluations.
The connector must not add a legend item, tooltip value, point, or evaluation
sample. Keep the tooltip's original issued/reconstructed/current labels.
Add a short caption explaining that adjacent prediction periods are connected
for display while their historical/issued provenance remains separate.

### Verification requested

- Cover all three periods and all three sales views.
- Test zero-valued endpoints, missing/null/non-finite endpoints, absent current
  prediction, missing immediate predecessor and omitted calendar buckets.
- Check week/year transitions, unequal month lengths and February in leap years.
- Assert the helper never mutates original points or changes evaluations.
- Browser-check the segment touches both plotted endpoints, the legend retains
  its three existing entries, and tooltips keep their original values/bases.
- Verify missing actuals, missing Combined components and unavailable saved
  predictions still leave genuine gaps. A `refresh_required` graph may retain
  original issues; draw a boundary only if both actual prediction endpoints exist.
- Run `npm test`, the repository's TypeScript checks and `npm run build`.

Backend-only verification and source review found no API/model defect requiring
an algorithm, schema or prediction-value change for this visual boundary.
The sibling frontend has not been modified in this maintenance pass.

Verified on 2026-10-27: the read-only backend check found consecutive calendars
and both boundary predictions in all nine period/view combinations (seven
weekly/monthly buckets; five yearly buckets). All 33 focused backend chart
tests passed in a disposable PostgreSQL database. The suggested helper passed
17 isolated cases; an in-memory Recharts render produced the SVG connector,
preserved zero/null values, and retained exactly three existing legend entries.
These were backend/reference-code checks. Upstream implemented the connector
in `d4d35f44`; its dedicated connector and rendering regressions were also
reviewed on 2026-10-28. The current local frontend matches upstream `49c3cb42`.
The 2026-10-30 source/component review confirms the newer seasonal, provenance
and graph-readiness displays; remaining gaps and staging limits are recorded
in `FRONTEND_HANDOFF_REVIEW.md`.

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

## Report availability and refresh handling

Frontend `d4d35f44` removed automatic explicit report refresh after checkout and
order writes while retaining mutation invalidation. Keep that behavior: a
report refresh failure must never resubmit or undo a successful sale.

The 2026-10-29 backend follow-up separates the per-account request budgets:
report previews have 120 requests/minute, PDFs have 30/minute, and forecast
reads retain their separate 30/minute budget. Forecast polling and PDF exports
therefore no longer consume the report-preview allowance. Preview/PDF 429s
still include `Retry-After`; retain cached content and respect that delay.

`POST /api/reports/refresh/` accepts an empty JSON object. Duplicate requests
from the same account, another account during cooldown, or while a full rebuild
is active now return HTTP **200**, preserving the existing success path. The
backend still limits full rebuilds to one per minute and serializes them across
processes. Business mutations invalidate data versions immediately; previews
use current ledger data even when a full rebuild is coalesced.

The response retains `message`, `generated_at`, and `report_types` and adds
`refresh_status`:

- `refreshed`: a full rebuild completed; `report_types` lists the rebuilt types.
- `coalesced`: another refresh was already requested; no duplicate rebuild ran.
  `report_types` is empty, `retry_after_seconds` describes the work cooldown,
  and `generated_at` is the last recorded successful rebuild timestamp, or null
  if that metadata is unavailable. It is not a fabricated new generation timestamp.

The current frontend already treats any 200 as success, emits REPORTS_UPDATED,
and invalidates its report/dashboard caches. No frontend code change is required
for this backend fix. Optional UI polish can display the returned message or
`refresh_status`. A cooldown remembered from the older 429 behavior expires
normally; a browser reload also resets that in-memory client state.

Keep the existing unconfirmed-sale guard and mutation-driven cache invalidation.
Verify two rapid refresh clicks, concurrent accounts, current previews/PDFs
after each sale, and protected export limits. Do not remove the backend rebuild
lock or use a new sale submission as a report retry.

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

A read-only `npm audit --json` on 2026-10-30 still reports **10 affected packages**
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
