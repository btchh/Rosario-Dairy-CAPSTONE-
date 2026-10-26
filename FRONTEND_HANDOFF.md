# Implement the current forecasting API in Jaymark's frontend

Updated 2026-10-26 (Asia/Manila).

Implement these changes in `JayMarkRocero/Rosario-Dairy-Capstone`. The backend
already provides the new graph API. This document is an implementation task for
the frontend developer; backend finalization does not change frontend code.

## Required implementation

1. In `src/features/dashboard/api/forecasting.service.ts`, add typed support for
   `GET /api/forecasting/graph/` with `period` and explicit `scope=real`. Switch
   `ForecastChart.tsx` to this call. Keep existing `/chart/` and `/forecast/`
   callers compatible where still needed; their payloads differ from `/graph/`.
2. Type the graph response, each view's `points`, nullable `message`, and
   `evaluation` keyed by `prediction_basis` using the contract below. Do not
   adapt the new response into the old approval/provisional fields.
3. Keep Combined, Regular Sales, and Milk Feeding tabs. All three views arrive
   in one response; switching tabs must not make another request. Fetch on
   period changes and use separate cache keys for each period and scope.
4. Render exactly three revenue series from each point: `past_prediction`
   (Past predictions), `actual` (Recorded sales), and `future_prediction`
   (Future predictions). Use `date` and `end_date` for labels and tooltips.
   Remove the old provisional/approved chart series and publication gate banner
   from this graph. A ready beta graph does not require production approval.
5. Render the complete server-provided centered calendar. Weekly/monthly have
   seven buckets; yearly has five. Do not anchor to the latest sale, apply the
   old eighteen-month history filter, or drop empty buckets. Mark `is_current`.
   Keep nulls as gaps (`connectNulls={false}`); zero is a valid recorded value.
6. Format amounts in Philippine pesos. In the current bucket, label actuals as
   partial when `is_partial_period` is true and identify the prediction as a
   full-period estimate. Tooltips must show the prediction basis: Original
   issued forecast, Reconstructed historical test, or Current forecast. Do not
   describe a reconstructed test as a forecast issued before the period.
7. Display evaluation separately for each returned basis, including the number
   of completed periods. Never merge issued forecasts and historical tests into
   one accuracy claim. Null WAPE means unavailable/undefined, including when
   actuals total zero. Do not score current partial buckets in the frontend.
   Below 15% regular monthly WAPE is a target, not a display gate.
8. For `refresh_required`, keep recorded sales visible, show the view's message
   when present, and offer manual refresh. Distinguish loading, request errors,
   insufficient history, and unavailable predictions. Do not replace unknown
   values with zero or trigger training through a GET request.
9. Keep the current asynchronous refresh workflow: POST `{}` once, accept 202,
   poll the returned relative `status_url` every five seconds, and invalidate
   graph caches for all three periods on completion. Recover queued/running
   jobs after reload. Handle failure, 409 and 429/`Retry-After`; avoid duplicate
   starts and stop polling on unmount. Add a readable label for the
   `refresh_beta_graphs` stage. Refresh success is not an accuracy guarantee.
10. Use real scope in the production dashboard. Demo scope is development-only
    and requires an explicit offline refresh; do not expose demo data as real
    forecast evidence.

## Acceptance checks

- Verify all nine period/view combinations against the graph API, with exact
  centered bucket counts, calendar boundaries, and three series.
- Verify null versus zero, partial current actuals alongside the full-period
  estimate, sparse/flat predictions, and issued versus reconstructed tooltips.
- Verify separate basis evaluations, null WAPE, `refresh_required`, no history,
  request errors and retry, and switching periods without stale cross-period data.
- Verify refresh start, duplicate prevention, reload recovery, success/failure,
  rate limiting, and cache invalidation for every graph period.
- Update service and UI tests for `/graph/`, explicit real scope and its actual
  response fields. Run `npm test` and `npm run build`. Report the test results
  and any remaining integration limits to the backend developer.

## Backend preparation

Before starting Django after pulling backend changes:

```bash
venv/bin/python manage.py migrate
```

A saved real graph for today can be prepared with:

```bash
venv/bin/python manage.py run_forecast_refresh
```

GET reads saved forecasts and never trains. The hosting provider must separately
run supervised midnight maintenance; starting Django does not run the scheduler.

## API contract to implement

## Authentication

Forecasting endpoints require an authenticated admin:

```http
Authorization: Bearer <access_token>
```

## Centered three-series graph

```http
GET /api/forecasting/graph/?period=monthly&scope=real
```

| Query | Values | Default |
| --- | --- | --- |
| `period` | `weekly`, `monthly`, `yearly` | `monthly` |
| `scope` | `real`; `demo2026` is development-only | `real` |
| `as_of` | ISO date anchoring the range | Today, Asia/Manila |

Real scope cannot use a future date. Production rejects demo scope with HTTP 400.
Development demo scope starts in 2026 and can simulate
another date without using simulated sales for training. Non-default dates
require a saved offline refresh for that date; GET never trains.

Ranges are centered on `as_of`, independently of the latest recorded sale:

| Period | Earlier buckets | Current bucket | Later buckets | Total |
| --- | --- | --- | --- | --- |
| Weekly | 3 weeks | This week | 3 weeks | 7 |
| Monthly | 3 months | This month | 3 months | 7 |
| Yearly | 2 years | This year | 2 years | 5 |

Weeks are Monday–Sunday. Months/years follow the calendar. Dates identify bucket
starts, with inclusive `end_date`. Values are Philippine-peso revenue, not units.

The response contains `scope`, `is_demo`, `training_scope: real`, `period`,
`as_of`, `available_periods`, `available_views`, `range`, `status`, `model_id`,
and `views`. `range` has `start_date`, `end_date`, `past_periods`, `future_periods`.
`status: ready` means a matching saved beta graph exists; it is not production
approval or an accuracy guarantee. `refresh_required` leaves recorded values
available, with unavailable estimates null. Beta predictions do not depend on
the older twelve-month production approval gate.

`views` contains `combined`, `regular`, `feeding`. Each entry has `label`,
`points`, `evaluation`, and a nullable availability `message`.

Each point contains:

| Field | Meaning |
| --- | --- |
| `date`, `end_date` | Bucket dates |
| `past_prediction` | Estimate for an earlier bucket; otherwise null |
| `actual` | Recorded sales through `as_of`; future buckets are null |
| `future_prediction` | Estimate for the current/later bucket; otherwise null |
| `is_current` | Bucket contains `as_of` |
| `is_partial_period` | Current actual total is partial, or a demo week crosses the 2026 dataset boundary |
| `prediction_basis` | `issued_forecast`, `historical_test`, `forecast`, or `fixed_origin_demo` |

The current bucket can contain both partial actual sales and a full-period
estimate. These are different quantities. Null means unavailable/unknown and is
not zero. No missing segment is substituted with zero to create a Combined
prediction; both component estimates are required.

`issued_forecast` is an immutable real forecast saved before the target began.
If none exists for a past bucket, `historical_test` is reconstructed using only
real data preceding that bucket. The basis distinguishes these cases; they are
not interchangeable evidence. Original issued values are retained after later
refreshes or ledger corrections. Current/future estimates are available in beta
without waiting a year for approval. Limited data can produce flat SARIMA paths;
no artificial variation is added.

Past issued predictions use the latest complete saved run for the target so
Combined and its two components remain consistent. If only component issues
exist, those remain available while Combined is null; a new sum is not presented
as a previously issued forecast. If validation sales total zero, percentage
error is undefined. A fitted local-level SARIMA baseline can still be returned
in beta without claiming validated accuracy.

## Evaluation and development-only comparisons

Outside production, an explicit `scope=demo2026` displays only non-voided generated transactions in the reserved
`2026:NNNNNN` namespace, dated in 2026 and no later than `as_of`. It never treats
real 2026 invoices as simulated. Demo fitting uses only real pre-2026 data;
simulated sales and later real sales cannot enter its model selection or fits.
Changing a dummy amount updates displayed results/evaluation while unchanged
training inputs reuse saved parameters. Demo predictions are hypothetical,
fixed-origin demonstrations, not forecasts proven issued before 2026.

`evaluation` groups completed visible buckets by `prediction_basis`. Each group
contains `periods`, `wape_percent`, `mae`, and `bias`. Current partial periods,
missing actuals, incomplete demo boundary weeks and missing predictions are excluded. WAPE is null when its
actual-value denominator is zero. Demo scores remain under `fixed_origin_demo`
and do not count toward real forecast accuracy or production evidence. Regular
monthly error below 15% is an improvement target, not a display blocker.

## Refresh lifecycle

```http
POST /api/forecasting/refresh/
Content-Type: application/json

{}
```

The body must be an empty object. A 202 response includes `job_id`, `status`,
`status_url`, `already_running` and `message`. `Location` identifies the job.
Manual refresh and midnight maintenance refresh all three centered periods for
real sales, anchored to the current business date. Demo runs are excluded from
normal maintenance and require an explicit development-only offline command.
Unchanged data reuses saved fits. Active work is shared rather than duplicated.

GET `/api/forecasting/refresh/` returns the latest job or
`{"status":"idle","job_id":null}`. GET its `status_url` returns the job's current
state. `queued`/`running` are active; `succeeded`/`failed` are terminal. Five-second
polling fits the read limit. A successful refresh updates saved graph results;
it does not guarantee lower error. Status responses also include timestamps,
stage, results and a nullable error code.

| HTTP status | Meaning |
| --- | --- |
| 400 | Invalid period/scope/date/body |
| 401 / 403 | Authentication required / admin permission required |
| 404 | Unknown refresh job |
| 409 | Maintenance is starting; check the latest job |
| 429 | Rate limit; observe `Retry-After` |
| 503 | Dependency unavailable; safe `detail` provided |

Refresh starts are limited to one per minute per account. GET does not fit.

## Existing endpoints

`/api/forecasting/chart/`, `/forecast/`, report previews and existing model/evidence
routes retain their previous contracts for compatibility. The new three-series
contract is `/api/forecasting/graph/`; it does not add another approval series.
Existing clients can continue using the old endpoints while adopting the new API.
