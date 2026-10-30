# Backend validation

## Hosting cleanup — 2026-10-30

Removed 37 generated Python bytecode directories (2,784,882 bytes) from the
source checkout. Kept private environments, business data, backups, migration
history, tests and operational tooling. Tightened `.dockerignore` to exclude
tests, host scheduler examples and host-only Docker tooling from the runtime
image while preserving the frontend build's Nginx input.

All 26 focused deployment/health/Redis-timeout tests passed again. Backend and
frontend verification images built successfully. Inspected the rebuilt backend
package for excluded artifacts and required entry points; all 48 application
migrations remain. Django system and pending-migration checks passed using the
rebuilt backend against the existing local services. No migrations were applied
or business writes performed. The running stack was not recreated for cleanup.
Reviewed candidate Git files for private/generated artifacts and credential
signatures; none were found. QA JSON evidence parses successfully.

## Full QA and debugging — 2026-10-30

Full results, remaining findings, evidence and scope are in
[QA_REPORT_2026-10-30.md](QA_REPORT_2026-10-30.md).

The initial full backend run completed 916 tests (915 passed, one Windows-only
skip); all 472 frontend tests, strict TypeScript and the frontend build passed.
A real QA Redis interruption exposed a slow availability response: 15.073
seconds, exceeding the frontend request timeout. One-second cache socket/connect
timeouts and no implicit retries reduced the measured safe 503 to 3.053 seconds.
The affected backend suites then completed 687 tests (686 passed, the same skip),
and 26 focused timeout/deployment checks passed. Counts overlap.

The guarded HTTP harness passed 169 scripted checks and 12 phases on the
disposable database, including malformed inputs, permission boundaries, 100
concurrent invalid writes, checkout/order UUID retry races, last-stock buyers,
cancellation races, atomic rollback, real password hashing and token rotation/
logout. All six report previews and PDF exports passed without creating forecast
model/job rows. Python image/npm advisory audits reported no known vulnerabilities.

All 2,148 read-load requests returned 200. Post-fix p95 was 1.563 seconds for ten
readers, and 3.794 seconds for twenty: the twenty-reader stage missed the
two-second target. This is a local capacity finding, not a deployment guarantee.

Real staff sales appeared in date-filtered admin history in 598 ms without
reload; another sale made offline appeared after reconnect. Date filters and
logout/revocation passed; stock moved from 10 to 7 for exactly two QA sales.
Opening unfiltered history with 4,861 receipts took 18–19 seconds and caused
excessive page reads, including a 97-page background order scan. The frontend
pagination/aggregate/cancellation request is in `FRONTEND_HANDOFF.md`; frontend
application source remains unchanged.

## Frontend pull, local Docker update and health check — 2026-10-30

Pulled frontend `63ecff33` from `9986a769` with a clean fast-forward. It adds
the shared live-event client, reconnect/session handling, cache-driven updates
to mounted queries and open records, and product-only inventory/outflow/alert
cleanup. All **472 frontend tests passed**, strict TypeScript checks passed,
and the production frontend build succeeded. No frontend source was edited
locally.

Rebuilt `rosario-dairy-frontend:local` and `rosario-dairy-backend:local`, updated
the local API/worker, added the matching ASGI events service and recreated the
gateway/frontend proxies. The running frontend image ID matches the new build
(`22ae35a3f14a…`); API and events share the rebuilt backend (`3d8a47ec0720…`).
The local stack combines `compose.yaml` with
`docker/compose.frontend.example.yaml`; the separate production definition
already includes the frontend under its optional `frontend` profile.

The frontend is healthy at `http://localhost:5174`. API/events/database/Redis
health checks, Django checks, pending-migration checks, both Nginx syntax checks
and the worker's latest tracked maintenance job passed. UI and SPA refresh
returned 200; all six compiled image/JS/CSS assets returned 200 with the correct
MIME types. Both user and event routes returned 401 without authentication,
through the frontend and API gateway. Authenticated frontend-proxied reads of
user, products, product batches and one-page sales history returned 200.

Two authenticated event readers through the gateway and frontend proxy received
a business-cache invalidation in **0.303 seconds**. Streams stayed uncompressed
and uncached. This checked the deployed notification path without creating a
sale, stock adjustment or account. The separate full-browser check for history
refresh with a preserved date filter timed out while host RAM/swap were under
pressure and remains unverified; the initial unfiltered browser probe did not
establish that acceptance criterion. No public deployment latency is claimed.

A private full database dump was taken before updating, and its archive contents
were checked with `pg_restore --list`. Previous running images were tagged for
rollback. Counts and content fingerprints for all **15 account/inventory/sales
tables** matched before and after the update and health checks. Existing database
and Redis containers/volumes were retained. No OVHcloud deployment, real SMTP
delivery or off-server backup/restore qualification was performed.

## Production Docker verification — 2026-10-30

Built `rosario-dairy-backend:production-test` from the current checkout and
passed **42 focused tests** covering production settings, restricted database
role checks, connection security, internal health probes, Compose credential
separation and startup gating. The test database was separate from application
data. The first isolated startup exposed the deliberate HSTS subdomain/preload
advisories blocking an overly strict preflight. The corrected preflight prints
those two advisories and blocks all other deployment warnings/errors; tests
cover both cases. Positive HSTS and the existing required HTTPS settings remain
enforced.

A disposable production Compose project with fresh volumes passed database
provisioning, every migration, static collection, preflight, API/events health
checks and the worker's initial forecast-maintenance run. Its empty history
correctly remained ineligible for production forecast approval. Repeat database
provisioning passed after migrations and application writes.

The actual runtime database account had all five elevated role flags disabled.
Every public table/sequence/view was owned by the migration role. Runtime CRUD
passed, while table creation, temporary-table creation, schema creation, table
alteration and truncation each failed with insufficient privileges. Separate
provisioning checks rejected elevated roles, inherited privileges and existing
objects owned by another role. These checks modified only the disposable stack.

The supplied TLS edge example was exercised with a temporary, CA-verified
self-signed certificate for `app.example.test`, substituting the test frontend
service for its host-loopback upstream. HTTPS requests traversed edge, frontend
proxy and API gateway. SPA refresh returned 200; unauthenticated user/events
requests returned 401 with HSTS; HTTP redirected to HTTPS; an unknown Host was
rejected at the edge. An incoming `X-Forwarded-Proto: http` did not override the
trusted HTTPS scheme. Eleven malformed login requests, each supplying a
different fake forwarded IP, produced ten 400 responses then 429: the edge
prevented spoofed IPs from bypassing the configured login throttle.

Two authenticated viewers received a staff checkout event through all three
proxy layers in **0.242 seconds** in the final warm sample (checkout response
0.100 seconds; both notifications received 0.141 seconds after that response).
Fresh history contained the sale and batch stock changed from 10 to 8 units.
SSE remained uncompressed, uncached and correctly allowed the configured HTTPS
origin. An earlier cold-start probe delivered both notifications but exceeded
its two-second target at 3.289 seconds; these are local samples, not a production
latency guarantee or an OVHcloud load test.

Gateway/frontend/edge Nginx syntax, local and production Compose configurations,
export-shell syntax and whitespace checks passed. The production application
image runs as the unprivileged `dairy` user with a read-only root filesystem;
container log rotation is 10 MB times three files. The disposable project,
including its database volumes and TLS edge, was removed after verification.
The existing local services/data and frontend checkout were preserved. No real
SMTP email was sent and no public deployment was performed.

Use `PRODUCTION_DEPLOYMENT.md` for the standalone deployment workflow. Actual
OVHcloud/domain/TLS setup, SMTP delivery, data transfer, full off-server backups
with a restore test, alerting and VPS capacity remain to be verified. The
frontend live-event client remains the request in `FRONTEND_HANDOFF.md`.

## Docker frontend packaging — 2026-10-30

Built frontend `9986a769` as `rosario-dairy-frontend:local` using the backend-owned
Node 24/Vite build and Nginx runtime. BuildKit named contexts copy only selected
frontend build inputs; the runtime image contains compiled assets, without
frontend source, host dependencies or environment files. The Linux amd64 image
is approximately 95 MB. No frontend application code was modified.

The frontend is healthy at `http://localhost:5174`, alongside native Vite on
5173. The local private Docker environment records `FRONTEND_PORT=5174`.
The existing backend containers stayed running. Chromium rendered the React
landing page; the compiled JPEG, JavaScript and CSS assets returned HTTP 200
with correct content types, and refreshing `/sales` served the SPA entry point.
The frontend proxy returned the expected unauthenticated HTTP 401 and successful
authenticated HTTP 200 reads for user, inventory and sales history endpoints.
Verification used an access token with the account's current token version and
did not create accounts, sales or stock changes. Nginx syntax, both frontend
Compose variants, the export script syntax and whitespace checks passed.

Build/start/update instructions are in `FRONTEND_DOCKER_HANDOFF.md`. This adds
container packaging; the live event client's frontend implementation remains
the separate request in `FRONTEND_HANDOFF.md`.

## Live updates across sessions — 2026-10-30

Added bearer-authenticated SSE at `/events/`, Redis notifications after business
cache invalidations commit, an internal ASGI service, and unbuffered gateway and
optional frontend-proxy routes. REST continues to use Gunicorn. No migrations,
business records or frontend application files were changed for this feature.
The frontend implementation request is in `FRONTEND_HANDOFF.md`.

The 18 focused tests passed with a separate PostgreSQL test database and an
explicitly supplied disposable Redis instance. They cover outer commit/rollback,
failed sales, notification/cache failures, voids/payments, token validity,
revocation, inactive accounts, password/role changes, two viewers, missed
publications, reconnect resync, unavailable streams, and a separate ASGI process
delivering a sale over authenticated HTTP with browser gzip/CORS headers. The
HTTP test requires delivery within two seconds in its local test environment.

The broader `config systemsetting sales inventory accounts` regression run
completed **595 tests: 594 passed, 1 skipped**, in 89.951 seconds, including the
Redis integration tests. It used the existing isolated test-database approach
and a fast MD5 password hasher configured only in the test-runner process;
application password/security settings were unchanged.

A new backend image built successfully. A separate Compose project with its own
database, Redis, volumes, Gunicorn, ASGI, gateway and stock Nginx frontend proxy
passed the complete HTTP smoke check. Two viewers connected through one and two
proxy layers received the staff sale notification in **0.281 seconds**; fresh
history included the sale and its product batch changed from 10 to 8 units.
The stream remained uncompressed and uncached with the expected CORS response.
This is a local measurement, not a production latency guarantee. Both Nginx
configurations and Compose validated, Django checks passed, and the migration
check reported no changes. The isolated smoke project was removed afterwards.
The existing application deployment and its business database were preserved.

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
