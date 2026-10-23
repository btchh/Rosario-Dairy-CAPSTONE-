# Forecasting API guide

Updated 2026-10-23 (Asia/Manila). API contract only; frontend implementation is
owned by the frontend team. No frontend files were edited.

Before running the system, always run `venv/bin/python manage.py migrate` from
the backend directory, including after pulling changes. Start the backend only
after migrations succeed.

## Authentication

Forecasting endpoints require an authenticated admin:

```http
Authorization: Bearer <access_token>
```

## Centered three-series graph

```http
GET /api/forecasting/graph/?period=monthly&scope=real
GET /api/forecasting/graph/?period=monthly&scope=demo2026
```

| Query | Values | Default |
| --- | --- | --- |
| `period` | `weekly`, `monthly`, `yearly` | `monthly` |
| `scope` | `real`, `demo2026` | `real` |
| `as_of` | ISO date anchoring the range | Today, Asia/Manila |

Real scope cannot use a future date. Demo scope starts in 2026 and can simulate
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

## Demo data and evaluation

`scope=demo2026` displays only non-voided generated transactions in the reserved
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
Manual refresh and midnight maintenance now also refresh all three centered
periods for both real and demo scopes, anchored to the current business date.
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
