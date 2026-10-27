# Outstanding frontend change requests

Updated 2026-10-27 (Asia/Manila).

Implement the outstanding changes below in
`JayMarkRocero/Rosario-Dairy-Capstone`. The backend support is ready.

**Work boundary:** make backend changes in this repository. Communicate frontend
change requests through this document; do not edit the sibling frontend
repository.

## Change request: connect adjacent past/current forecast predictions

**Frontend implementation pending.** The backend returns consecutive calendar
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
These checks verify the contract and proposed code; applying and browser-testing
the frontend implementation remains pending.

## Change request: registration field length limits

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

## Change request: apply custom dates to inventory and forecast reports

The current frontend deliberately drops the selected dates for `inventory` and
`sarima_forecast`. Its date validator rejects both report types. The backend now
supports dates for all six report types; update the frontend to pass them through.

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
  archived research models, fabricate historical predictions, or trigger fitting.
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

## Audit follow-up: historical receipt amount labels

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

## Audit follow-up: refresh after successful sales

`checkout.service.ts` currently awaits `reportsService.refreshAfterMutation()`
after every successful sale. Other mutation callers should be reviewed too.
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
validated predictive accuracy. Research scores and the below-15% WAPE target
must not be presented as an achieved live accuracy figure. Preserve the existing
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
