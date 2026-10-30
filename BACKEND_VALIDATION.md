# Backend load and input validation — 2026-10-29

## Product-only inventory cleanup — 2026-10-30

Supplier, ingredient and ingredient-batch models/services are removed. Stock
adjustments/counts require a product batch; product FEFO, sales, cancellations,
reports, settings and alerts remain supported. Legacy collection GETs return
empty lists for older frontend callers; retired writes/details return 410.

Verification used a separate PostgreSQL test database with an in-memory cache
and a fast test-only password hasher. No application security setting was changed.
`manage.py test inventory config reporting systemsetting sales accounts --noinput`
ran **653 tests: 652 passed, 1 skipped**, in 99 seconds. This includes the new
product-only request/constraint tests and migration tests that verify empty
retired tables are removed, product stock survives, and populated legacy data
stops the migration before schema removal. This is regression evidence; the
October 29 load measurements below were not repeated for this cleanup.

The backend image rebuilt successfully. After a verified private database dump,
`inventory.0012_product_only_inventory` was applied to the local Docker database.
Canonical row counts/content hashes for all **19 protected inventory, sales and
account tables** matched before and after. The three obsolete tables are absent,
and product references on stock adjustments/counts are non-null. Business cache
payloads were invalidated before restarting API and forecast workers. Private
database exports and comparison files remain outside Git.

Nineteen authenticated HTTP smoke reads passed through the Docker gateway,
including product inventory, movements/counts, alerts, sales/customer lists,
legacy empty lists, and the inventory preview/PDF. The database still contains
170 products, 198 product batches, 324 customers and 4,835 transactions, with no
2026 recorded transactions. Django checks, migration checks and database/cache/API
health passed; the unauthenticated user endpoint correctly returned 401.

The crow's-foot ERD, metadata, data dictionary, SVGs and eight-page A3 PDF were
regenerated outside the backend repository in `../docs 2/`. All **47 tables and
48 foreign keys** are covered by the detailed modules. Frontend source was not
modified; removing its obsolete calls, DTOs and labels is a handoff request.

## Earlier load measurements

The backend met the requested **10-user test workload** after input-validation
fixes and a forecast ledger-read optimization. This is a measured development
check, not a production capacity guarantee. The 25-client burst stayed available
but missed its latency target.

## Measured HTTP results

Same Docker API configuration as the normal local stack: Gunicorn with two
workers and two threads each, Nginx, PostgreSQL and Redis. Tests ran in a separate
Compose project on the shared i3-6100U / 8 GB development laptop. The database
was a copy of the cleaned 4,835-receipt dataset plus disposable audit fixtures.
Each concurrent client used a separate authenticated test account.

| Workload | Requests | HTTP errors | p50 | p95 | Maximum | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 10 clients, 60 seconds, 0.5-second think time | 595 | 0 | 145 ms | 1,803 ms | 4,246 ms | Met 2-second overall p95 target |
| 25 clients, 10 requests each, no think time | 250 | 0 | 1,226 ms | 6,818 ms | 11,293 ms | Exceeded 5-second burst p95 target |

The mix reads products, customers, 50-receipt history pages, settings and monthly
forecast graphs. It includes initial requests after restarting the API; it does
not discard cold requests. A client has one request in flight at a time. Several
parallel requests from one browser can therefore produce a larger burst than
the user count. Individual endpoint tails remain slower than the overall p95;
see the raw report for the breakdown.

The first 10-client sample had no HTTP errors but missed the latency target.
Profiling found that a graph request extracted the ledger five times. Graph
contexts now share one source read across their date cutoffs. Their completed
weekly, monthly and yearly response hashes matched exactly before and after.
No cross-request forecast cache was added: subsequent reads still detect
backdated edits and other ledger corrections even without invalidation signals.
The initial sample overlapped the CPU-heavy full test suite, while the final
sample did not. The timing difference alone does not measure optimization speedup.

## Invalid inputs and concurrent writes

- **405 input probes** returned the expected validation/not-found responses.
  Cases include missing fields, JSON containers in scalar fields, invalid IDs,
  decimal precision and overflow, NaN/infinity, invalid dates, empty/duplicate
  carts, invalid payment methods and malformed request UUIDs.
- **100 invalid checkouts across 10 clients:** all returned JSON 400 responses;
  transaction, retry-key and stock snapshots remained unchanged.
- **10 concurrent retries with one request UUID:** one 201 and nine 200 responses,
  all referring to one receipt; stock deducted once.
- **10 buyers competing for 3 units:** three sales and seven validation failures;
  stock ended at zero without overselling.
- **10 cancellation attempts:** one success, nine validation failures, one stock
  return.
- **Write rate limiting:** 30 invalid attempts returned 400, the next two returned
  JSON 429 with a positive Retry-After header; no business changes occurred.
  Cache-backed throttles are not an exact concurrent quota mechanism.

Malformed IDs on inventory reactivation routes previously caused HTML 500 errors.
They now receive the existing JSON 404 error response. Orders and transaction
filters now reject zero, negative and out-of-range customer/user IDs with 400
instead of silently returning an empty success result. Valid numeric IDs and
numeric strings retain their behavior.

Existing response shapes remain in use: serializer field errors, `error`, or
`detail`, depending on the endpoint. Authentication/permissions use 401/403;
conflicting reuse of a sale UUID uses 409; a database availability failure uses
503 with a safe message. A sale timeout or 503 is not permission to submit a new
sale automatically: verify its status or retry with the same request UUID.
Checkout/order request IDs remain optional for compatibility; unkeyed submissions
cannot be deduplicated by the server.

## Regression coverage

The full suite passed **894 tests, one skipped**, after the input fixes. After
snapshot batching and the last five added tests, **174 targeted regression tests
passed**, including source extraction, graph cutoffs/origins, primary/weekly
model integration, invalid inputs and checkout concurrency. These counts overlap.
The final source changes have no schema migrations. Frontend source was unchanged.

Both normal databases retain 4,835 transactions, 324 customers, 170 products,
198 historical reference batches and their original two accounts. Recorded 2026
sales/orders/stock activity remains empty, and forecasts remain available.

## Report availability follow-up

Reports now have independent per-account budgets: 120 preview requests/minute,
30 PDF exports/minute, and a separate 30/minute forecast-read allowance. Preview
and export requests retain finite limits and safe cache-outage responses.

Manual refresh requests accept only an empty JSON object. The existing account
work limit and global rebuild lock/cooldown still prevent duplicate full work,
but repeated requests return 200 with `refresh_status: coalesced` instead of
blocking the UI with 429. Cached generation metadata is the last real completion
time, or null if unavailable; a coalesced response does not claim a new rebuild.
Business data versions still change immediately after committed writes.

The report follow-up regression run passed **671 tests, one skipped**.
Coverage includes independent quotas, real export protection, malformed
refresh bodies, rapid refreshes, multiple accounts, a refresh active on another
PostgreSQL connection, previews remaining accessible, and cache failures.
Docker HTTP checks also passed all six previews and PDF exports, ten rapid
refreshes (one rebuild, nine coalesced responses), cross-account reuse and
independent forecast/export quotas.
The follow-up 10-client, 60-second run added daily-sales and inventory report
previews to products, customers, 50-receipt history pages and monthly graphs.
All **695 requests returned 200**, with overall **p95 1.44 seconds**, p99 2.62
seconds and a maximum of 4.72 seconds. Both report-preview endpoints had p95
under 1.30 seconds. There were no rate-limit responses. Report builders were
warm from the smoke checks; catalog/history requests included first per-user
cache reads. This is a different mix from the earlier run, not a controlled
performance-speedup comparison.

The previous HTTP load samples above describe the earlier five-endpoint mix;
additional report-inclusive measurements are recorded in the detailed JSON.

## Repeat the HTTP read check

Use a separate test stack and database copy; the tested port was 18000. Prepare
a private JSON array containing a valid access token for each distinct test
account, keep that file outside Git, and run from the backend repository:

```bash
python3 scripts/load_check.py \
  --base-url http://127.0.0.1:18000 \
  --tokens-file /private/path/tokens.json \
  --concurrency 10 --duration 60 --think-time 0.5 \
  --max-p95-ms 2000 --output /private/path/read-10-users.json
```

The script uses only GET requests. It checks JSON responses and HTTP status,
measures full-response latency, and exits nonzero on any failed request or an
exceeded overall p95 target. Its report contains no tokens or response bodies.
Use repeated `--path` arguments to test other read endpoints. Respect per-account
read throttles when changing the mix/rate; 429 responses are reported as failures
in the normal traffic sample rather than counted as successful reads.

The one-minute sample does not qualify a long-running soak, sustained mixed
writes, large imports, PDF export bursts, model fitting under concurrent HTTP
traffic, or a larger production database. Recheck on deployment hardware and
a real network before raising the supported load.

Detailed sanitized results: [validation/backend-load-2026-10-29.json](validation/backend-load-2026-10-29.json).
Private test credentials, response logs and database copies are excluded from Git.
