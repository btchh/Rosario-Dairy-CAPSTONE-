# Backend validation

## Frontend pull, Docker update and confirmed OTP delivery — 2026-10-31

The user confirmed that requesting an OTP now delivers email after Gmail SMTP
activation. The agent did not send a diagnostic email or trigger a reset.

Pulled frontend `305bdf49` to `7295f413` with `git pull --ff-only` (two commits,
clean checkout). All **580 frontend tests** passed in 12.232 seconds and
`tsc --noEmit` passed. Rebuilt/deployed only the frontend service, retaining the
working backend image because this frontend has no idle-session integration.
The frontend image is
`sha256:7f7018565b65d122c8272e73eafc7a7c7c459315875b3b816842b89d5db3c243`,
tagged `rosario-dairy-frontend:frontend-7295f413-20261031` and `:local`, with
`:before-frontend-7295f413-20261031` preserving the previous image. Container
health passed. No database migration or backend source promotion occurred.

Authenticated Chromium checks against the actual Docker frontend passed for
both admin and staff: encoding tables requested server pages, Entered by
displayed plain account names, transaction details showed Payment updated by,
and paid records exposed no Update payment action. No browser runtime exceptions
or business writes occurred in these checks. Temporary read-only access tokens
and browser profiles were removed; no real user session was logged out.

History integration is partial: default admin history/encoding reads are paged,
but search/sort use the complete-list path, as do staff sales history and both
Orders pages. `FRONTEND_HANDOFF.md` records these remaining requests and the
still-missing idle activity/warning/logout client. Current backend runtime
remains the compatible payment-updater image with Gmail SMTP configured.
SMTP configuration/command/Compose checks were rerun before push: **36 passed**.

Logs: `/tmp/frontend-release-tests-20261031.log`,
`/tmp/frontend-release-types-20261031.log`,
`/tmp/frontend-release-build-20261031.log`,
`/tmp/frontend-release-browser-20261031.log`, and
`/tmp/backend-smtp-release-tests-20261031.log`.

## Docker SMTP wiring and safe verification — 2026-10-31

The running local API was confirmed to use the console mail backend, with no
SMTP host/login/password configured. Local Compose did not forward SMTP
settings. It now forwards optional SMTP host, port, login, password, TLS/SSL
and sender into the application services; database/Redis/gateway receive no
SMTP credentials. Production Compose already forwarded these values. Blank
local SMTP settings retain console development with a valid default From;
complete SMTP credentials select SMTP, and partial credentials fail startup.

Added `python manage.py check_smtp` to reject console/test mail as a delivery
check and authenticate SMTP with sanitized errors. Its default mode sends no
message and issues no OTP. Only an explicit `--send-to` sends a diagnostic
email; SMTP acceptance still requires inbox confirmation. Public recovery
responses remain non-enumerating and no account or OTP behavior was changed.
Local and production runbooks now distinguish API health, SMTP authentication
and delivered email, and document environment recreation instead of restart.

Focused configuration/command/Compose regression: **36 tests passed**.
Broader accounts/config/sales/inventory/reporting/systemsetting regression:
**737 discovered, 736 passed, one Windows-only ACL skip**, in 148.326 seconds,
using disposable PostgreSQL 18 and Redis. Coverage includes actual Django SMTP
backend calls with mocked transport for TLS, login, no-send checks, explicitly
requested diagnostic mail, rejected senders, invalid recipients, sanitized
provider failures, empty/complete/partial configuration, and real Compose
environment resolution. Existing OTP, auth, inventory, payment, reporting,
live-event and deployment regressions passed. No real email was sent.
Migration drift, Compose validation, Python syntax and diff checks passed.
Logs: `/tmp/backend-smtp-config-tests-20261031.log` and
`/tmp/backend-smtp-regression-20261031.log`.

**SMTP activated locally after the user supplied the App Password privately.**
The ignored, mode-0600 `.env.docker` identifies the user-confirmed Gmail sender
`thoushaltnotrespond@gmail.com` and Gmail port 587 with TLS. Removed Google's
display-format spaces from the supplied App Password without displaying its
contents. A disposable one-off container using the deployed image passed TLS
connection and SMTP authentication before activation. Recreated API/events/
worker with the existing image and reloaded the gateway; the running API then
passed the same authentication check. No email was sent and no reset challenge
was issued by these checks.

All three application services retain image
`sha256:2a63d72d59647a22ccc05376e7a7e5d7b6b7bf1163e0d7945145e8a2d1f4e7b6`.
No new image or migration was applied. API/events became healthy, the worker
is running, Django checks and the deployed image's migration check passed.
HTTP checks verified the frontend returns 200 and its proxied unauthenticated
user/events routes return 401, as do the direct gateway routes. The new idle
environment variables are harmless on this older image; pending idle-session
source/migration and the new `check_smtp` command remain undeployed until
frontend integration. Runtime SMTP verification used Django's existing mail
connection directly, without depending on the newer management command.

The earlier search of project configuration, archived files, saved environment
files, shell configuration and Git stashes did not recover the old password.
Older tracked revisions used Brevo, but the user confirmed their working setup
used Gmail; no provider switch was made. No credential contents were printed.
The user subsequently confirmed actual OTP delivery. OVHcloud release
requirements remain unverified.

## Idle-session enforcement and frontend request — 2026-10-31

Added database-backed `LoginSession` and migration `accounts.0005_loginsession`.
Login issues a per-sign-in `sid`, copied into access JWTs and retained through
refresh rotation. Protected authentication and refresh check the saved idle
deadline. Default policy is 900 seconds, with a 60-second frontend warning;
validated environment settings are exposed in both Compose definitions and
templates. Missing/malformed/unknown/foreign sessions require a new sign-in.
Existing JWTs will require one-time reauthentication at release.

`GET /accounts/session/` reports the deadline without extending it.
Authenticated `POST /accounts/session/activity/` with an object body extends
only a valid session, under a row lock and with server-owned timestamps. The
activity route is limited to 30 requests/account/minute. Protected reads/writes,
background polling, live connections and refresh never register activity.
Exact expiry and a second check after initial authentication prevent revival.
Database failures retain the sanitized 503 behavior. Separate sign-ins have
separate idle deadlines; existing explicit account-wide logout and
password/deactivation revocation remain unchanged. Live streams use their
existing authentication recheck cycle to close expired sessions.

Regression: **727 discovered, 726 passed, one Windows-only ACL skip**, in
119.186 seconds. Ran accounts/config/sales/inventory/reporting/systemsetting
coverage against disposable PostgreSQL 18 and Redis, including live ASGI HTTP
delivery and open-stream idle expiry. Tests cover both roles, background reads
and rotated refresh not extending expiry, the exact deadline, nonresurrection,
server-time enforcement, session isolation, legacy/invalid/foreign sessions,
fresh login after expiry, rejected business writes, activity's second deadline
check, explicit logout, malformed/unauthenticated activity and safe database
failures. Existing real-JWT fixtures now issue tracked sessions. A stale
live-publication test was corrected from disallowed paid-to-pending to valid
pending-to-paid; production payment behavior was unchanged.

Django checks, migration-drift checks, both Compose configuration checks and
`git diff --check` passed. Test log:
`/tmp/backend-idle-regression-20261031.log`.

**Source implementation only; running application images/database unchanged.**
Frontend source was not edited. `FRONTEND_HANDOFF.md` requests trusted input
reporting, warning/continuation UI, multi-tab coordination, resume/offline
expiry and terminal 401 cleanup. Ship the migration, backend and frontend
integration together; the existing frontend would otherwise expire even an
actively used session because it does not send activity reports. This work
does not claim completed browser acceptance or a deployed idle-logout feature.

## Plain encoder names and visible payment updater — 2026-10-31

The user clarified Sales Encoding should keep its Entered by header and show
just the encoder's username, without the repeated Encoded by/role prefix. This
supersedes the earlier suggestion to rename that column Cashier. The frontend
cell currently calls `encodedByLabel()`; the latest handoff instructs it to use
`handled_by.username` directly and show payment updater/time in details.

Added output-only nullable `payment_updated_by_user: {id, username, role}` to
transaction/encoding responses and full/compact linked order transactions.
Existing `payment_updated_by` ID and `payment_updated_at` remain compatible.
Missing updater data returns null and is not inferred from the encoder/viewer.
The field reports the last recorded payment-state writer, including initial
recording, not a full historical event log. Histories eager-load this user to
avoid per-row queries; API cache namespace v8 retires the prior response shape.
No model/data migration was added.

Affected regression: **295 discovered, 294 passed, one Windows-only skip**, in
45.570 seconds, using disposable PostgreSQL and an isolated cache/test hasher.
Coverage verifies a different encoder/updater across history/detail/customer
and linked-order APIs, nullable identity, read-only fields, spoof rejection,
paid no-op replay preserving updater/time, and bounded history query counts.
The earlier paid-finalization and cashier tests also pass.

Built/deployed `rosario-dairy-backend:payment-updater-20261031` to local
API/events/worker, retaining `before-payment-updater-20261031` for rollback.
API/events health, Django checks and pending-migration verification passed.
Read-only authenticated HTTP checks through the frontend proxy verified SI1093
retains encoder/cashier ADMIN, reports payment updater STAFF and the stored
`2026-10-31T14:22:49.949357+08:00` timestamp, and remains Paid/noneditable.
Transaction/encoding list/detail and linked order payloads agree; summaries
also return 200. All 19 working account/inventory/sales tables retained
identical row counts/content hashes. No test payment writes ran against those
tables. The disposable test service was removed afterward. Frontend
source/image remain unchanged, so the visual wording/payment-audit display is
still a frontend task documented in the handoff.

## Paid payment finalization — 2026-10-31

The user requested payment details become read-only once Paid. The payment
service now checks the current status under the existing transaction row lock.
For both admin and staff, a change to an already-paid status/method/tender is
rejected with HTTP 409 and `code: payment_locked`. Identical paid retries return
200 without rewriting values, recomputing recorded change or replacing the
original payment updater/time. Paid-on-creation and later-finalized entries use
the same rule; pending updates remain available. No unlock route was added.

`can_update_payment` is a computed read-only boolean on transaction/encoding
responses and full/compact linked order transactions. Paid, voided and
nonmanual records return false. API cache namespace v7 retires older response
shapes. No schema migration or stored payment rewrite was needed.

Affected regression: **293 discovered, 292 passed, one Windows-only skip**, in
47.220 seconds. Disposable PostgreSQL, isolated test cache and a fast test
hasher were used. Coverage includes locked cash/online details for both roles,
direct-service protection, pending-to-paid transitions, paid-on-creation,
unchanged no-op replay audit, history/capability propagation, and two concurrent
different finalizations producing one 200 and one 409 with the winning payment
and updater preserved. The prior paid-to-pending expectation was replaced by
the new finalization rule. The test service was removed after verification.

Built/deployed `rosario-dairy-backend:payment-lock-20261031` to the local
API/events/worker and retained `before-payment-lock-20261031` for rollback.
API/events health, Django checks and pending-migration verification passed.
Authenticated GETs through the frontend proxy verified SI1093 is Paid,
`can_update_payment: false`, cashier ADMIN, consistently in transaction
list/detail, encoding and linked orders. Summary reads also passed. Production
payment PATCHes were not used for testing; all rejection/concurrency writes ran
in the disposable database. All 19 working account/inventory/sales tables kept
identical before/after row counts and content hashes.

Frontend source/image remain unchanged. Its existing payment action is still
rendered for paid rows; the backend now rejects edits. The latest
`FRONTEND_HANDOFF.md` section instructs the owner to hide/close the editor for
paid/noneditable records and handle stale 409s. Its earlier suggestion to rename
Entered by to Cashier is superseded by the latest plain-name clarification above. The current label
identifies the original invoice encoder, not the later payment updater. UI
acceptance remains pending frontend implementation.

## Manual encoder shown as cashier — 2026-10-31

The user corrected the earlier display rule: a locally encoded sale should show
its encoding admin/staff member in Cashier. `TransactionNumberMixin` now returns
that user in `recorded_cashier`; the original encoded-by label remains.
Workbook imports still return null, because their import owner does not
identify a cashier. No saved identity, amount, date or historical row is changed.
API cache namespace v6 retires cached null-cashier payloads. Existing frontend
mapping already supports the nonnull user; frontend source/image were unchanged.

Affected sales/cache/backend-fix regression: **289 tests, 288 passed, one
Windows-only skip**, 49.801 seconds, using disposable PostgreSQL and an isolated
test cache/fast test password hasher. Coverage verifies admin/staff encoders for
both viewer roles across transaction/encoding list/detail, customer history,
full/compact linked orders and encoding creation, plus imported-cashier
preservation. The earlier missing-cashier expectation was updated to the user's
new rule. The separate test database service was removed after verification.

Built `rosario-dairy-backend:cashier-20261031` and applied it to local
API/events/worker, with the prior image retained as
`rosario-dairy-backend:before-cashier-correction-20261031`. This release includes
the pagination summary/filter implementation below. API/events health, Django
system and pending-migration checks passed; no migrations were applied.
Authenticated read-only HTTP requests through the existing frontend proxy
verified SI1093 returns cashier ADMIN in transaction list/detail, encoding and
linked orders. Transaction/order summary routes also returned 200. Before/after
row counts and content hashes were identical for all 19 account/inventory/sales
tables. The frontend image was not rebuilt; a fresh read receives the corrected
field. Browser rendering was not separately asserted for this correction.

## History pagination filters and summaries — 2026-10-31

Added authenticated read-only `/sales/transactions/summary/`,
`/sales/orders/summary/` and `/sales/encoding/summary/` endpoints. Lists and
summaries share date, customer, handler, payment and literal search filters;
orders also filter fulfilled/cancelled status. Lists support stable server
sorting and retain their existing pagination/response shapes. Summary amounts
are exact decimal strings from recorded invoice totals, independent of paging.
Pending-payment order counts include only fulfilled, nonvoid linked receipts
awaiting payment. Existing role/category visibility and commit-based cache
invalidation remain in force. The API cache namespace changed to prevent older
responses that ignored the new filters from being reused.

`sales/test_history_api.py` adds 18 tests, including a 4,801-receipt/order fixture
whose summaries execute one aggregate query without serializing receipts.
Coverage includes equal-value and large totals, paid/pending/unknown amounts,
voided/cancelled/unlinked orders, inherited encoding scope, search, stable sort,
Manila day boundaries/backdated receipts, malformed inputs, existing error-field
compatibility, authorization, hidden/mixed category access and cache refresh.

Final regression: **706 discovered; 705 unique tests passed; one Windows ACL
test remains skipped.** The six-app run (`sales`, `config`, `reporting`,
`inventory`, `accounts`, `systemsetting`) took 106.023 seconds: 703 passed,
three skipped. The two Compose checks skipped inside the container then passed
on the host with the Docker CLI (0.527 seconds). Live-event integration tests
ran with an explicitly supplied disposable Redis broker. Database tests used
a separate ephemeral PostgreSQL container, isolated test cache and a test-only
fast password hasher. No working business data or running application services
were modified. Django system checks passed; `makemigrations --check --dry-run`
reported no changes. The temporary test services were removed afterward.

Frontend checkout `305bdf49` passed all 535 tests and TypeScript during the
alignment review. Frontend source was not edited. Integration steps and API
examples are in the 2026-10-31 section of `FRONTEND_HANDOFF.md`; the older
review is explicitly marked historical. At this initial source verification,
the running backend/frontend images had not been rebuilt. The subsequent local
backend deployment is recorded above. Frontend adoption and end-to-end latency
verification on representative data remain pending; the query test is not a
deployment-capacity claim.

## Discount feature removal; historical data retained — 2026-10-30

New checkout, orders and manual encoding reject active discounts for both
roles, including direct service calls. Final entered prices determine new
totals; neutral defaults from older clients remain compatible. Discount fields
are absent from receipt/order/customer-history APIs and report previews/PDFs.
Response cache namespaces changed to retire older payload shapes.

Existing model columns, source import/refresh handling, recovery exports and
stored amounts remain unchanged. No data/schema migration was added;
`makemigrations --check --dry-run` reported no changes. Regression fixtures
verify every legacy sale/item/order/batch row remains identical after history
and report reads, and later payment uses the original recorded invoice total.
Ordinary pre-removal UUID fingerprints still replay their original receipts.

The affected config/accounts/sales/inventory/reporting/settings/scheduling run
found 693 tests: 686 passed, six Redis tests required an explicit disposable
broker, and one required native Windows ACL APIs. All six Redis tests were then
run successfully against a separate Redis instance: 692 unique tests passed,
with only the Windows test unverified. The main run took 73.758 seconds; the
Redis run took 5.618 seconds. Tests used a separate ephemeral PostgreSQL
container and a test-only fast hasher; working services/data were not changed.
The backend verification image built and passed Django checks with its Redis
cache configuration. Frontend source remains unchanged; complete UI/report
removal, historical-value preservation and saved-draft requirements are in
`FRONTEND_HANDOFF.md`.

Local Docker follow-up: applied the verified backend image (`2b4042d846a8…`)
to API/events/worker after saving a private database dump and rollback image.
No migrations were applied. The API/events probes passed and the gateway was
reloaded; the frontend container and source were unchanged. Authenticated
daily/weekly/monthly previews and PDF exports through the existing frontend
proxy all returned 200. Extracted text from all three fresh PDFs contained no
Discount rate, Discounts or Gross before discounts cards. All 17 account,
inventory and sales tables retained identical row counts and content hashes.
The OVHcloud deployment has not been performed. Release review retained the
isolated regression results above and rechecked the running API/events probes,
Django system checks and migration state. Frontend source remains unchanged.

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
