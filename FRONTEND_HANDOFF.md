# Frontend handoff and completion status

Updated 2026-10-31 (Asia/Manila).

**Overall status: partially satisfied.** Frontend `305bdf49` is now checked out.
Backend pagination support is implemented below; the frontend still needs to
consume it. The local backend was rebuilt and deployed on 2026-10-31 with the
cashier correction, paid-payment lock, payment-updater identity and summary/filter
endpoints; the frontend image still predates the latest pull. Earlier dated reviews below
describe their original checkouts and are not the status of the latest source.
These displays do not imply production forecast approval. The idle-session backend
below is a newer source change and has not been deployed to the running stack.

**Work boundary:** make backend changes in this repository. Communicate frontend
implementation requests through this document; do not edit the sibling frontend
repository during backend maintenance.

Latest frontend pull: `305bdf49` on 2026-10-31. It removes discount presentation,
preserves recorded decimal amounts, adds encoder/payment-status labels, cancels
obsolete history reads, and loads staff KPI groups independently. Source review
found these API expectations compatible with the backend. All **535 frontend
tests** and strict TypeScript checks passed on this checkout. Full-list history
and order downloads remain; use the new contract below to replace them. The
local Docker frontend image still predates this pull.

Previous deployed frontend: `63ecff33` on 2026-10-30. It implements the live-update
client and product-only inventory cleanup requested below. The rebuilt local
Docker frontend is running at `http://localhost:5174`, with the matching backend
and ASGI events service. All 472 frontend tests and strict TypeScript checks
passed. Isolated QA subsequently completed the interrupted browser checks:
staff sales refreshed filtered admin history, filters survived offline/reconnect,
and logout revoked access. Unfiltered history still has the performance issue
requested below. No frontend source was edited locally.

The earlier detailed review inspected frontend `origin/main` at
[`49c3cb42`](https://github.com/JayMarkRocero/Rosario-Dairy-Capstone/commit/49c3cb42)
on 2026-10-30, after pulling three upstream commits with a clean working tree.
This review did not modify frontend application code, tests, dependencies or
configuration.

## Idle logout: backend ready, frontend implementation requested — 2026-10-31

**User request:** automatically sign out users who stop interacting. Backend
source now enforces a **15-minute idle limit** for both admins and staff. Request
a **one-minute warning** before expiry. These defaults are configurable with
`AUTH_IDLE_TIMEOUT_SECONDS=900` and `AUTH_IDLE_WARNING_SECONDS=60`; both Compose
definitions pass them through. The timeout must be positive and greater than
the nonnegative warning. Read the effective values from the API, rather than
hardcoding a separate frontend policy.

**Release coordination:** this change adds migration `accounts.0005_loginsession`.
Apply it before starting the new backend. Existing JWTs lack a session ID and
will require a fresh sign-in once the backend is deployed. Deploy the frontend
integration with this backend: the current frontend sends no activity reports,
so even an actively used browser would expire 15 minutes after sign-in. The
running local backend/frontend images have deliberately not been replaced by
this change. Previous sections' deployment statements describe earlier changes.

Each successful sign-in creates a database-backed session. Its signed `sid`
claim is copied into access tokens and retained by refresh-token rotation.
Expiry affects that sign-in, including all tabs sharing its credentials;
another independently signed-in device has a separate idle deadline. Ordinary
API requests, status checks, polling, live-event heartbeats/reconnects and token
refreshes **never** extend the deadline. The API rejects protected reads/writes
and refreshes at or after expiry; activity cannot revive an expired session.
Live streams recheck authentication on their existing heartbeat/change cycle
and close with `auth_required` (normally within 15 seconds of expiry). Existing
password/deactivation revocation remains in force. Explicit manual logout
retains its existing account-wide revocation behavior.

**API contract** (paths relative to the configured API base; with the local
proxy, prefix `/backend`):

- `POST /accounts/login/` and `POST /accounts/refresh/` retain `access`/`refresh`
  and add a nested `session` object with the shape below. Save both rotated
  tokens as before. Refresh does not renew the idle deadline.
- `GET /accounts/session/` returns that object directly, with
  `Cache-Control: no-store`. Use to restore/check session state without
  registering human activity.
- `POST /accounts/session/activity/` with JSON `{}` returns the same object
  directly and extends a still-valid session from the server's current time.
  Bearer access authentication is required. The server ignores submitted
  session IDs/timestamps/deadlines; clients cannot update another session.
  The activity endpoint has an account limit of 30 requests/minute. Coalesce
  events across tabs; do not send a request for every mouse move/keystroke.

```json
{
  "id": "7bd62574-cd6e-4644-aedd-6c04415095bd",
  "idle_timeout_seconds": 900,
  "warning_seconds": 60,
  "server_time": "2026-10-31T08:00:00+00:00",
  "last_activity_at": "2026-10-31T08:00:00+00:00",
  "idle_expires_at": "2026-10-31T08:15:00+00:00"
}
```

Idle rejection is **401** with a stable top-level code:

```json
{"detail": "You were signed out due to inactivity. Please sign in again.", "code": "session_idle_timeout"}
```

Missing/malformed/deleted/foreign sessions return **401** with
`{"detail": "Please sign in again.", "code": "session_required"}`.
An ordinarily expired access JWT still follows the existing refresh flow,
provided its idle session and refresh token remain valid. Database failures
fail closed with the existing sanitized **503**, not an authentication failure.

**Frontend implementation requested; do not mark complete until accepted:**

1. Extend login/refresh types with `session` and add status/activity service
   methods. On restored credentials, read session status. Keep the current
   session-generation guards on every asynchronous result; discard responses
   from a prior sign-in/account. Use the server deadline and server-time offset
   for countdowns, including delayed responses; do not rely only on a fresh
   local 15-minute timer. Accept only confirmed deadlines for the same session;
   do not overwrite a later confirmed deadline with an older in-flight response.
2. Track trusted pointer/touch, keyboard and scroll interactions while the app
   is visible. Post activity promptly for interaction and coalesce continuing
   activity (roughly once per 30 seconds, with a final debounced report after
   interaction stops). A timer may flush observed activity, but must never post
   simply because it fired. Loading a page, token refresh, cache/live updates,
   tab focus/visibility changes and network reconnect alone are not activity.
   Coordinate reports across same-session tabs to stay below the server limit.
   The backend cannot distinguish real input from a falsely reported heartbeat;
   this frontend event discipline is required for actual human inactivity.
3. Show a countdown dialog when `warning_seconds` remain. Provide **Stay signed
   in** and **Sign out**. Stay signed in sends activity immediately and dismisses
   the warning only after success; it cannot recover an expired session. Keep
   drafts intact while warning. A 503/network failure must not invent a new
   deadline; retry only while the last confirmed session is still valid. A 429
   must respect Retry-After and retain the confirmed deadline.
4. At the confirmed deadline, clear credentials, current user, private caches,
   drafts and live subscriptions, then show the login page with **You were
   signed out due to inactivity. Please sign in again.** Recheck absolute time
   after sleep/resume or a suspended tab; delayed browser timers must not allow
   access or a business submission after expiry. Synchronize confirmed activity
   deadlines and expiry across tabs by session ID, without extending deadlines
   solely from local messages or signing out a newer/different session.
5. In `src/lib/api.ts`, treat `session_idle_timeout` and `session_required` as
   terminal 401s: clear the session and notify auth listeners without attempting
   refresh or retrying a business write. Apply this to failed refresh responses
   too. Use the same centralized cleanup for `auth_required` from live events.
   Keep ordinary access-expiry refresh and temporary-outage behavior. Automatic
   idle cleanup should clear local state without calling the existing global
   logout endpoint, which would also revoke independently active devices.
   An explicit user Sign out action may use the existing logout endpoint.
6. Verify both roles: no input plus continuous polling still expires; real
   activity extends only the matching sign-in; warning/Stay signed in works;
   expiry during an open editor rejects submission; refreshed JWTs cannot
   bypass idle expiry; active/idle tabs share one deadline; a separate device
   stays signed in; sleep/resume, offline expiry, reconnect, account switching,
   stale in-flight responses and the one-time rollout re-login behave correctly.
   Rebuild the frontend image after implementing and testing these changes.

## Entered by wording and payment updater identity — 2026-10-31

**Latest user clarification:** keep the **Entered by** column in Sales Encoding
and show **only the encoder's account name** in its cells, for example `ADMIN`.
Do not repeat `Encoded by`, `Entered by`, or the role prefix inside that column.
Use `transaction.handled_by.username`, with `Not recorded` only when genuinely
missing. Keep the exact account name; do not replace every administrator's name
with the generic role Admin. This supersedes the earlier suggestion to rename
the column Cashier. The Cashier display in sales history remains a separate
column/context. Do not replace the encoder with the current viewer or the
person who later changed the payment.

**Backend implemented:** transaction/encoding responses and full/compact linked
order transactions now include the read-only nullable field
`payment_updated_by_user: {id, username, role} | null`. The existing
`payment_updated_by` numeric ID/null and `payment_updated_at` timestamp are
retained unchanged for compatibility. Example of distinct actors:

```json
{
  "handled_by": {"id": 7, "username": "ADMIN", "role": "admin"},
  "payment_updated_by": 12,
  "payment_updated_by_user": {"id": 12, "username": "staffname", "role": "staff"},
  "payment_updated_at": "2026-10-31T14:00:00+08:00"
}
```

Here Entered by shows `ADMIN`; payment details show **Payment updated by:
staffname** and the supplied timestamp in Asia/Manila. The payment updater is
the last recorded payment-state writer, including the initial recorder when
there has not been a later update. It is not a full event history. Preserve a
null updater as `Not recorded`; do not guess from the encoder, cashier, viewer,
payment method or Paid badge. Identical paid retries keep the original updater
and timestamp. The new field is output-only; clients cannot choose the updater.
No database migration or historical identity rewrite is required. API cache
namespace v8 retires earlier response shapes; history queries eager-load the
updater to avoid one extra database read per row.

**Frontend implementation pending:**

1. In `SalesEncoding.tsx`, retain header `Entered by` and replace the cell's
   `encodedByLabel()` text/title with `sale.transaction.handled_by?.username`.
   Plain names should fit the existing column. Other source labels can remain
   in their appropriate details/history contexts.
2. Add `payment_updated_by_user?: DjangoUserSummary | null` to
   `DjangoTransaction` and its compact order Pick. In `receiptDetails.ts` and
   transaction details, show **Payment updated by** from its username and the
   existing **Payment updated** time. Render the available actor even when the
   update occurred within the same second as creation; the existing timestamp
   comparison must not suppress the identity. For initial payment recording,
   neutral `Payment recorded by` wording is also accurate. Missing actor/time
   stays `Not recorded`/`Not provided`, never an inferred account.
3. Verify admin/staff encoders and a different payment updater: the Entered by
   cell remains the encoder's plain username, while details show the actual
   last updater/time. After Paid, the payment remains read-only. Live cache
   updates should refresh details without replacing the original encoder.

**Applied locally:** the backend image was rebuilt/deployed; API/events health,
Django checks and pending-migration verification passed. Authenticated proxy
GETs for SI1093 show **Entered by ADMIN** and **Payment updated by STAFF**, with
the saved update time `2026-10-31T14:22:49.949357+08:00`, across transaction and
encoding history/details and linked orders. It remains Paid and noneditable.
All 19 business-table counts/content hashes were unchanged. The affected
295-test regression passed 294 tests with one Windows-only skip. Frontend
source/image were not changed, so the plain-name cell and visible payment audit
still require the frontend implementation above.

## Paid payments are final — 2026-10-31

**Latest user requirement: once Paid, payment details are read-only for both
admins and staff.** This supersedes earlier behavior/tests that allowed marking
a paid encoding pending again. The backend locks the transaction row before
checking the saved status. A pending/unknown manual payment may still be
completed; after it becomes paid, status, payment method, tender, change and
payment audit cannot be replaced through the payment endpoint. This applies
equally to entries created paid and entries marked paid later.

`PATCH /sales/encoding/{id}/payment/` returns **409** for a requested change to
an already-paid payment:

```json
{"error": "Payment is already marked as paid and is read-only.", "code": "payment_locked"}
```

An identical paid retry returns **200** with the existing saved values and
original updater/timestamp. That is a no-op for recovery after a lost response,
not permission to edit a paid payment. No second payment is written and no stock
or sale totals change. Existing validation, visibility and voided-sale checks
remain. There is no admin bypass or unlock endpoint.

Transaction/encoding responses and linked order transactions now include the
read-only boolean **`can_update_payment`**. It is false for paid, voided and
nonmanual records; otherwise true for a locally editable manual payment. It
updates to false in the successful pending-to-paid PATCH response. The API cache
namespace changed to v7 so older response shapes are not reused. No schema
migration or historical payment rewrite is needed.

**Frontend implementation pending; backend work does not hide the current UI
button.** In `PaymentUpdateControl.tsx`, hide the Update payment action and
editor whenever `can_update_payment === false` or `payment_status === "paid"`.
Keep the manual/voided guards, and add the optional boolean to `DjangoTransaction`
and the compact order type. For compatibility with an older backend that omits
the flag, derive editability from manual, nonvoid and nonpaid status. Recheck
before saving; when a live update or successful PATCH makes the record paid,
close the editor immediately and show its saved payment details read-only.
Handle `409`/`payment_locked` by retrieving fresh data and closing the editor;
do not offer a retry that changes paid values. Preserve form/session guards.
Verify a second user's already-open editor cannot overwrite the first user's
payment, and that identical timeout recovery does not replace payment history.

**Why "Entered by Admin" appears:** the Sales Encoding table hardcodes an
`Entered by` header and renders `encodedByLabel()`, which describes the account
that originally encoded the invoice. It is independent of `payment_updated_by`;
recording payment later does not change who entered the sale. The latest user
clarification keeps **Entered by** as the header and shows only
`handled_by.username` in that cell, as detailed above. The source/encoder label can remain in
receipt details and beneath SI numbers as already requested. No API identity
change is required for the wording; do not substitute the payment updater or
the current viewer as cashier.

**Backend applied locally:** API/events health, Django checks and
pending-migration verification passed after the image update. Read-only proxy
GETs verified SI1093 is Paid with `can_update_payment: false` and cashier ADMIN
across transaction/encoding history and linked orders. All 19 business-table
counts/content hashes remained identical. The affected regression passed
292 tests with one Windows-only skip; competing different finalizations were
tested in disposable PostgreSQL and produced one 200 and one 409. No test
payment writes ran against the working business database. See
`BACKEND_VALIDATION.md`. Frontend source/image remain unchanged, so hiding the
button, showing the plain Entered by name and displaying the payment updater
are still frontend tasks.

## Backend pagination and summaries ready — 2026-10-31

**Backend implemented; frontend integration pending.** No schema migration or
historical-data rewrite is required. The routes are relative to the configured
API base: with the local proxy, `/sales/transactions/` becomes
`/backend/sales/transactions/`. The local backend now serves the summary routes;
other deployments must include this backend update before calling them.
Existing list/detail/write response shapes remain compatible.

### Paged rows and filters

Use one GET per selected table page. The response remains
`{count, next, previous, results}`; `count` is the complete filtered row count.

| Route | Default page size | Maximum page size |
| --- | --- | --- |
| `GET /sales/transactions/` | 50 | 200 |
| `GET /sales/orders/` | 50 | 500 |
| `GET /sales/encoding/` | 50 | 200 |

All three lists and their matching `/summary/` routes share these filters:

| Parameter | Contract |
| --- | --- |
| `start_date`, `end_date` | Optional inclusive `YYYY-MM-DD` bounds in Asia/Manila; either can be supplied alone. Transactions/encoding use the receipt's effective `created_at`, including backdated encoding. Orders use the linked receipt's effective date, falling back to order creation when unlinked. |
| `customer_id` | Positive customer ID. |
| `handled_by` | Positive handler user ID. For transactions/encoding this is the recorded processor/encoder; for orders it is the order handler. It is not necessarily the original cashier. |
| `payment_method` | `cash`, `online`, or `unknown`; orders filter their linked receipt. |
| `payment_status` | `paid`, `pending`, or `unknown`; orders filter their linked receipt. |
| `search` | One trimmed, case-insensitive substring, maximum 200 characters. Matches recorded invoice/SI number, linked customer name, or handler username. Orders also match an exact positive numeric order ID. It is a literal search, not a regular expression; no product/item search or fabricated display placeholders such as `Not recorded`. |

Transaction/encoding lists and summaries additionally accept
`include_voided=true` (default false). Order lists and summaries additionally
accept `status=fulfilled|cancelled` (default all). `pending` is a **payment**
status, not an order status. Orders retain cancelled/unlinked rows in the list;
there is no order `include_voided` filter. Payment filters exclude unlinked
orders because they have no recorded payment.

Lists accept `page`, `page_size`, and `ordering`. Choose **one** ordering field:
`id`, `created_at`, `transaction_date`, `transaction_number`, `total_amount`,
`customer`, or `handled_by`; orders also accept `status`. Prefix with `-` for
descending. Default is `-created_at`, with a stable ID tie-breaker in the same
direction and nulls last. `created_at` orders by the row creation timestamp;
`transaction_date` orders by the effective receipt date/fallback described
above. `transaction_number` uses the saved SI string, not a numeric conversion.
Use `ordering=-id` to preserve the current frontend's order-ID ordering.
Invalid dates/ranges, IDs, filter choices, overlong search and unsupported
ordering return HTTP 400. Other undocumented parameters are not supported
filters and must not be assumed to affect results.

Example: `GET /sales/orders/?page=1&page_size=25&status=fulfilled&payment_status=pending&ordering=-id`.

### Transaction and encoding summaries

`GET /sales/transactions/summary/` returns the complete filtered result:

```json
{
  "transaction_count": 3,
  "total_revenue": "125.65",
  "paid_count": 1,
  "paid_amount": "100.00",
  "pending_count": 2,
  "pending_amount": "25.65",
  "unknown_count": 0,
  "unknown_amount": "0.00"
}
```

`GET /sales/encoding/summary/` returns the same shape restricted to manually
encoded receipts. These are read-only GET endpoints for authenticated admins
and staff. Page and ordering parameters do not affect summaries. All money
fields are exact two-decimal **strings**, including zero and sums beyond the
precision of one invoice. Preserve them through the frontend's recorded-money
helpers; do not round-trip through `Number` for display or summation.

`total_revenue` is the sum of recorded final invoice totals across **all**
matching payment statuses, including pending and unknown. The state amounts
partition that total. By default voided receipts are excluded; explicitly
including them also includes their recorded amounts, so do not label that
result as active revenue. No invoice totals are reconstructed from item prices.

For admin Today's Sales/yesterday comparisons, request this summary once for
each date with matching `start_date` and `end_date`. For staff My Sales Today,
also send `handled_by=<current user ID>`. The API keeps shared staff history
available; it does **not** implicitly restrict staff to their own records.

### Order summary and dashboard counts

`GET /sales/orders/summary/` returns:

```json
{
  "order_count": 7,
  "fulfilled_count": 5,
  "cancelled_count": 2,
  "pending_payment_count": 1,
  "total_revenue": "60.60"
}
```

Counts cover all matching orders, including legacy unlinked orders.
`pending_payment_count` counts only **fulfilled orders with a linked, nonvoid
transaction whose payment status is pending**. It does not count standalone
checkout/encoding receipts, cancelled orders, paid/unknown payments, voided
receipts or unlinked orders. `total_revenue` sums recorded linked transaction
totals for fulfilled, nonvoid orders only; it excludes cancelled and unlinked
orders. Counts are integers and money is a two-decimal string. Summary values
reflect any supplied filters, independently of pagination or ordering.

Use unfiltered `pending_payment_count` for the admin Pending Orders card; replace
both the `KPICards` fallback scan and any parent-provided count derived from
`ordersService.getAll()`. Use `handled_by=<current user ID>` and
`fulfilled_count` for staff fulfilled-order counts. Retain independent loading
states for the staff KPI groups; sales must not wait for order downloads.

### Frontend implementation and verification remaining

1. Replace `salesService.getAll()` / `ordersService.getAll()` page chains in
   visible history/order tables with page-specific service methods. Pass
   search, sorting and filters to the server; sorting/filtering only a local
   page would produce incomplete results. Use the server `count` for paging.
2. Load full-result cards from `/summary/`, never by summing the current page.
   Send the same filters when a card describes the filtered table. If a card
   intentionally shows lifetime totals (for example the current staff lifetime
   card), issue a separate summary with only its identity/visibility scope and
   keep the lifetime label. Do not accidentally redefine it as filtered totals.
3. Include the user/session, complete filters, search, ordering, page and page
   size in table cache keys; summary keys include identity and all their own
   filters. Reset to page 1 when filters/search/order change. On live updates,
   refresh the visible page and relevant summaries; if cancellation removes the
   last page, return to a valid page. Preserve filters, selection and form drafts.
   A selected detail outside the refreshed page can use the existing detail GET.
4. Keep AbortSignal propagation, account/epoch guards, reconnect/resync and
   coalesced fallback polling. Summary/list reads use the existing business-data
   cache and write invalidation; no explicit report-refresh POST is needed.
   Staff lists and summaries exclude entire hidden/inactive-category or mixed
   receipts/orders before counting or summing. Never replace server totals with
   client estimates for missing/failed summary responses.
5. After integration, rebuild the frontend image and verify both
   roles through the real proxy: filtered/unfiltered initial load, search/sort,
   payment updates, cancellations, concurrent writes, reconnect and navigation.
   Include this backend image in other deployments. Use the representative
   4,800+ receipt dataset and record latency. Source/unit
   verification does not establish end-to-end or deployment capacity.

Backend regression coverage is in `sales/test_history_api.py`: summaries beyond
one page and beyond one invoice's money precision, literal search, stable
ordering, Manila day boundaries, order business-date fallback, invalid inputs,
authentication, encoding scope, hidden/mixed category visibility, cache refresh,
and a 4,801-row aggregate/page check. Tests run against disposable PostgreSQL
with a private in-memory test cache; the working business database is not used.
Final regression verification: **705 unique backend tests passed**, with one
Windows-only ACL test unverified. The 706-test run had three skips; its two
Docker CLI checks were subsequently run successfully on the host. Live events
were tested with disposable Redis. Django system and migration-drift checks
passed. Details are in `BACKEND_VALIDATION.md`. These are source/API tests;
frontend integration and rebuilt-stack browser checks remain pending.

## Discount removal from the system — 2026-10-30

**Backend implemented; frontend changes requested. Existing data must remain
exactly as stored.** This supersedes earlier discount controls and historical
receipt-display requests. The user explicitly reaffirmed that frontend source
must remain untouched during this backend task.

The backend rejects percentage/fixed/nonzero discounts for new checkout,
orders and manual encoding, for both admins and staff. It uses the entered
final selling price directly. Discount fields are removed from transaction,
item, order and customer-history responses, report previews and report PDFs.
New report payloads omit `discounts`; `product_revenue_basis` now identifies
`recorded_line_amounts`. Cache namespaces changed so old response shapes are
not served after release. No database migration or historical rewrite is part
of this change: existing discount columns, values, invoice totals, quantities,
prices and source records are retained, including private recovery exports.
Historical workbook import/refresh tooling retains its source-data handling.
The local Docker backend now runs this change. Fresh daily/weekly/monthly
report previews and PDFs were verified through the existing frontend proxy;
PDF text has no discount cards or rate. Frontend source and its container were
not changed, so the UI removal below remains outstanding.

**2026-10-31 source update:** frontend `305bdf49` implements the removal and
recorded-money display requested here, including guards for legacy pending
submissions. Its 535 tests and TypeScript pass. Browser/PDF/print verification
against rebuilt images remains pending. The following is the original request
against `63ecff33`, retained as the acceptance checklist:

1. Remove discount selectors, value inputs, permissions/prompts, calculations
   and summaries from every checkout, order and manual-encoding form. Remove
   the `canDiscount` path in `SaleComposer`/`SaleLineEditor`; simplify
   `manualPricing` to quantity times the entered final selling price. Omit
   discount fields from new request payloads and frontend API types. The
   backend tolerates old clients' neutral `none`/zero defaults during the
   transition; active discounts return 400 and must never be silently ignored.
2. Remove discount rows, badges, columns, rates, explanatory discount text and
   report cards everywhere, including old receipt/history/order displays.
   Update `receiptDetails`, admin/staff order details, report types/mappings,
   exported/printed frontend receipts and any report preview UI. Remove the
   notification advice to discount expiring stock; retain FEFO/dispatch advice.
   The user requested removal from the system's presentation, including reports.
3. Keep all saved historical values and totals authoritative. Do not erase
   stored discounts, add them back to old totals, reprice older invoices, or
   assume an absent API field means the database was rewritten. Show recorded
   invoice/line amounts as supplied. Historical line and invoice totals may
   differ; explain that using neutral recorded-amount wording where needed.
4. Handle saved/unconfirmed drafts explicitly. Never strip a discount and
   resubmit a changed total under an old request ID, or issue a new sale before
   checking whether the original succeeded. Keep duplicate-sale protections.
   Ordinary undiscounted backend UUID fingerprints remain compatible; an old
   active-discount draft requires history verification and a deliberate new
   entry at the final selling price, rather than automatic conversion.

Acceptance: admin/staff desktop and mobile flows have no discount controls,
calculations or output. API writes cannot apply discounts. Sales/history and
report displays still show the exact recorded invoice totals, and original
stored discount data remains unchanged. Verify old receipts, pending/paid
updates, saved drafts, PDF/print output and live refresh alongside the encoder
and payment-status labels below. No frontend application files were changed.

## History labels: encoder and payment status — 2026-10-30

**2026-10-31 cashier correction requested by the user:** local manually encoded
sales must show the encoding admin/staff member in the **Cashier** column.
The backend now returns `recorded_cashier: {id, username, role}` from
`handled_by` for those records, consistently across create/replay, history,
detail, customer history and linked orders. This supersedes the earlier
instruction below to leave manual encodings' cashier unknown. It is a display
contract for the local processor, not a claim about an unrecorded paper-invoice
cashier. Workbook imports with `source_reference` still return
`recorded_cashier: null` because their import owner is not a known cashier.
No historical rows or amounts are rewritten; the API cache namespace changes
to retire cached null-cashier responses. The current frontend already prefers
an explicit `recorded_cashier.username`, so this correction needs no frontend
source edit. Keep the **Encoded by Admin/Staff — username** source label and
payment badge. For example, SI1093 encoded by ADMIN displays **ADMIN** in Cashier
and **Encoded by Admin — ADMIN** under the SI number.

Applied locally: rebuilt the backend and recreated API/events/worker, retaining
the prior image for rollback. Authenticated GETs through the existing frontend
proxy verified SI1093 returns **ADMIN** in transaction list/detail, encoding and
linked orders; summary endpoints also returned 200. API/events health, Django
system and pending-migration checks passed. All 19 account/inventory/sales tables
retained identical row counts and content hashes. The affected backend run
passed 288 tests with one Windows-only skip (289 discovered). Frontend source
and image were not changed; refresh the existing page to retrieve the corrected
cashier field. No browser-render assertion was performed for this correction.

**2026-10-31 source update:** frontend `305bdf49` now displays encoder labels and
payment-status badges in history/mobile views using the existing backend fields.
The following is the original request; verify it through the rebuilt frontend.

**Frontend changes requested by the user. Backend fields already exist; no
database migration or API change is required.** An administrator's manually
encoded sale must be identified in history, and unpaid transactions must show
their pending payment status without opening the details dialog.

Frontend `63ecff33` currently shows payment method (Cash/Online) in admin/staff
history rows and `MobileSaleCard`. Payment status is already available in
`TransactionDetails`, the encoding list and customer history. Manual receipts
are titled "Encoded Sales Record" in details, but the encoder is not shown in
the history row. That version left manual Cashier cells as "Not recorded";
the 2026-10-31 correction above supersedes this behavior.

Required display:

- For `is_manually_encoded === true`, show an encoding label in the history
  row/card and details, using the transaction's `handled_by.username` and
  `handled_by.role`. Example: **Encoded by Admin — encodingadmin**. Use the
  actual encoder's role, rather than the viewer's role. Staff encodings should
  likewise identify their encoder. `encoded_at` supplies the actual entry time;
  `transaction_date`/`created_at` remains the effective sale date.
- For local manual encoding, display `recorded_cashier.username` in Cashier;
  the backend supplies the encoder's identity as requested on 2026-10-31.
  Identify manual encoding through `is_manually_encoded`, not
  `handled_by.role` alone. Imported historical records retain their existing
  source classification and unknown-cashier behavior.
- Display **Pending**, **Paid**, or **Not recorded** from `payment_status`
  (`pending`, `paid`, or `unknown`/missing) alongside the payment method. Reuse
  `PaymentStatusBadge` in admin/staff sales history and mobile cards. A Cash or
  Online payment method does not establish that payment has been received.
  Preserve the existing status display in customer history and receipt details.
- Refresh these labels on the existing sales cache invalidation, including an
  open detail dialog. Payment updates must change the badge on the existing
  transaction without creating another receipt. Preserve filters, selected
  records, pagination and existing role/category visibility.

Contract: `POST /sales/encoding/` returns a shared history transaction with
`is_manually_encoded`, `encoded_at`, `handled_by: {id, username, role}` and
`payment_status`. Those fields are also returned by `GET /sales/transactions/`,
transaction detail, encoding history and customer transaction history. Encoding
defaults to `pending` when omitted. `PATCH /sales/encoding/{id}/payment/` updates
payment status and invalidates shared sales/history caches.

Acceptance: an admin encodes a pending sale and another authorized user's open
history shows **Encoded by Admin — username** and **Pending**, on desktop and
mobile, without reload. Marking that same sale paid changes its badge to
**Paid** and preserves encoder identity. Repeat for staff encoding, ordinary
checkout and unknown legacy payment status; do not mislabel either the original
cashier or the transaction source. These requirements can be implemented with
the existing payload and shared badge component. No frontend source was edited
in this backend task.

## QA follow-up: history and dashboard pagination — 2026-10-30

**2026-10-31 update:** the backend filter/aggregate contract is now implemented
in the section **Backend pagination and summaries ready** above. Frontend
`305bdf49` implements obsolete-read cancellation and independent staff KPI loads,
but retains full-history reads. The server pagination and summary integration
requested here remains outstanding; use the explicit contract above.

**Frontend changes requested after full QA of `63ecff33`.** The real-sale browser
flow passed: a staff sale reached date-filtered admin history in 598 ms, another
sale made offline appeared after reconnect, the date filter survived and logout
revoked access. However, opening unfiltered history with 4,861 receipts took
18.279–19.164 seconds. The first run observed 52 history HTTP responses by initial
render and 97 background order-page requests. The complete results are in
`QA_REPORT_2026-10-30.md` and `validation/full-qa-browser*.json`.

1. Replace full-list `salesService.getAll()` / `getAllPages()` reads in visible
   history/order tables with server pagination. Keep full-result summaries and
   search correct: do not sum/search only the current page while presenting them
   as complete totals/results. Existing APIs paginate transactions (maximum
   page size 200) and orders (maximum 500); coordinate any additional search,
   aggregate and order-filter contracts with the backend before using them.
   Unsupported query parameters must not be assumed to filter server results.
2. Replace the admin `KPICards` fallback that downloads every order to count
   fulfilled orders whose transaction has `payment_status === "pending"`.
   Request an appropriate server aggregate; preserve that payment-count meaning
   and existing role/category visibility. It is not the count of an order
   workflow status named pending, and it must not be replaced with zero.
3. Cancel obsolete paginated reads when navigating, changing filters, switching
   accounts or invalidating an in-flight query. Pass an abort signal through
   the HTTP/page-loading layer. Avoid completing an old whole-history chain
   before starting its replacement after `ready`/resync. Keep required resync,
   epoch/revision guards, deduplication and draft-form preservation.
4. Preserve filters, selected records and valid pagination during live updates.
   Verify both filtered and unfiltered views with at least the copied 4,800+
   receipt dataset and concurrent staff writes. Test reconnect/fallback without
   repeatedly downloading every page from inactive screens.

Staff dashboard follow-up: the user observed sales appearing late after a
successful POS transaction, then confirmed they did load. `StaffKPICards`
currently waits for today's sales, every order and inventory together through
`Promise.all`, with a 30-second deadline. Before that resolves, empty fallback
arrays can display zero sales while the card says Updating. Load KPI groups
independently, show a loading state until their own data arrives, and replace
the complete order scan with an appropriate server aggregate. Preserve the
current staff identity/category scope and Asia/Manila day boundaries. Verify a
successful POS sale appears without waiting for unrelated order-page downloads.

Acceptance: the first usable history page and KPI counts must not require
exhausting the complete receipt/order histories. Record end-to-end initial and
live-update latency on representative data. The sub-second filtered QA sample
does not establish the same latency for the current unfiltered screen.
No frontend source changes were made during this backend QA task.

## Docker frontend packaging — 2026-10-30

**Implemented in the backend repository; rebuilt for frontend `63ecff33`.** The Node/Vite
build and Nginx runtime are defined in `docker/frontend.Dockerfile`, with the
source build context supplied by `docker/compose.frontend.build.yaml`.
`docker/compose.frontend.example.yaml` adds the frontend image to the existing
backend network. See `FRONTEND_DOCKER_HANDOFF.md` for build/start/update commands.
No frontend application files were changed. The frontend image uses `/backend`
for API calls and already has the server-side proxy route for the live event
stream. Frontend `63ecff33` now supplies the client connection described below.

## Live updates across sessions — 2026-10-30

**Backend and frontend implemented; local service/HTTP integration verified.**
Frontend `63ecff33` adds one authenticated fetch stream at the application
boundary, cache-group invalidation, reconnect/session handling, visible-page
polling fallback and refreshed open records. The contract and acceptance checks
below remain the reference. The staff/admin browser history flow, date-filter
preservation, offline resync and logout passed isolated QA. The unfiltered
history performance follow-up above remains outstanding.

Transactions made by
staff must appear on an administrator's open history screen without reloading
the page. The existing frontend's three-minute freshness timer does not meet
that requirement. This request covers sales history, orders, customer history,
inventory and the corresponding dashboard/report summaries.

### Backend contract

- `GET /events/` on the API origin, or `/backend/events/` with the optional
  same-origin frontend proxy. Send `Authorization: Bearer <access_token>` and
  `Accept: text/event-stream`. Use the existing API base URL. Never place JWTs
  in URLs. Native `EventSource` does not accept an Authorization header; use
  an authenticated `fetch` stream (or a streaming client supporting headers).
- Admin and staff accounts can connect. Existing API reads still enforce role,
  ownership and product visibility. The stream contains only cache groups and
  an opaque version, never transactions, customer details, prices or stock counts.
- SSE fields are UTF-8 lines terminated by a blank line. Parse across arbitrary
  network chunks, including split characters/lines and multiple frames in one
  chunk. Ignore comment heartbeats and unknown event types. `retry: 3000` is a
  suggested reconnect delay in milliseconds, not an application event.
- `ready` always requests a resync, on first connection and every reconnect:

  ```text
  event: ready
  data: {"groups":["sales","orders","inventory","categories","customers","dashboard","reports","settings","users"],"version":"opaque-value","resync":true}

  event: data_changed
  data: {"groups":["sales","orders","inventory","categories","customers","dashboard","reports","settings","users"],"version":"different-opaque-value"}

  ```

  The `users` group is included for administrators only. Other groups are broad
  invalidation hints, not a promise that each group changed. The opaque version
  is for change detection only; do not sort it or use it as a transaction ID.
- `data_changed` follows committed business changes, including sales, orders,
  voids, manually encoded payments, stock changes and model-backed settings.
  Notifications are coalesced over approximately 150 ms. Bulk operations which
  bypass model signals must call the existing `invalidate_data_cache()` helper.
  No live connection starts forecasting or retrains models.
- `: heartbeat` comments keep connections alive. Every 15 seconds the server
  also checks the current session and shared data version, recovering missed
  Redis publications. Streams rotate after five minutes or access-token expiry.
  Reconnects always require resync; there is no event replay or `Last-Event-ID`
  cursor. Current API data, not the stream, is the source of truth.
- `reconnect` with `{"reason":"renew_connection"}` means renew/reopen the
  connection. `auth_required` with `{"reason":"session_expired_or_changed"}`
  means the session expired, was revoked, or changed permissions: use the
  existing session/refresh handling and stop retrying if refresh is rejected.
- `unavailable` with `{"retry_after":5}` means retry with backoff and use
  fallback polling. EOF and transport failures require the same recovery.
  Before streaming starts, missing/invalid access tokens return 401, unsupported
  roles return 403, and unavailable dependencies/configuration can return 503.
  WSGI/runserver cannot serve the stream: use the Docker gateway or ASGI.

### Frontend implementation contract

1. Open one shared stream per authenticated application tab, at the layout/auth
   boundary, rather than one per widget. Abort it on logout, unmount, or account
   change. Ignore late events and HTTP results from a previous session. Reuse
   the existing single-flight token refresh flow; retain rotated token pairs.
2. On `ready` and `data_changed`, invalidate the supplied recognized cache groups
   through `src/lib/dataCache.ts`. This must bypass the three-minute freshness
   window so mounted `useCachedQuery` subscribers fetch promptly. Coalesce bursts
   and deduplicate requests. Preserve existing revision guards so a response
   started before an event cannot overwrite fresher data.
3. Update visible sales/order rows, summary totals, inventory and customer
   history using fresh API responses. Preserve search, date/payment filters,
   pagination where valid, scroll position, selections and unfinished sale forms.
   Refresh open detail dialogs as appropriate, rather than retaining stale row
   objects. Keep existing data visible during background requests.
4. Reconnect with capped exponential backoff and jitter. Always resync after a
   reconnect or return from offline/sleep; never assume missed events are
   replayed. Renew an expired token before reopening. Treat 403 as an access
   change, not an infinite retry loop. A reconnect must not resubmit a sale.
5. While streaming is unavailable, poll the active operational screens every
   five seconds when visible and online. Suspend that fallback when streaming
   recovers, and avoid frequent full-history reads from hidden tabs or every
   dashboard widget independently. Display a short connection message only if
   useful; do not expose protocol/server details in normal product screens.

Acceptance: with separate staff/admin sessions, completing a sale updates the
admin's open history, totals and affected inventory automatically. Target roughly
1–2 seconds in normal conditions; measure through the deployment proxies rather
than claiming a guaranteed latency. Verify failed/rolled-back sales do not appear,
voids/payments refresh, filters/forms survive, multiple widgets share one stream,
and logout, expiry, role changes, network interruption, reconnect and fallback
polling all recover without duplicate submissions or cross-session data.

### Deployment dependency

The backend Compose stack adds an internal `events` ASGI service on port 8001;
the existing gateway routes only `/events/` to it. REST calls continue to use
Gunicorn. Redis must be shared between writers and the event service. Both the
gateway and optional frontend Nginx examples disable stream buffering/caching;
apply the same behavior to any OVHcloud TLS proxy in front of them. Use HTTPS in
production. Rebuild/export the backend image before distributing this contract.
For a separately hosted frontend, configure its exact HTTPS CORS origin and keep
the stream on the API origin. No frontend application files were changed here.

## Client wording revision — 2026-10-30

The client needs a sales-planning screen, with short explanations of what is
available and what action is needed. The earlier request to expose extensive
model evidence, publication gates and provenance in the normal client interface
is withdrawn. **This section supersedes the detailed display instructions below
where they ask for research diagnostics on the client screen.** Keep those API
fields and their meaning for developer documentation, capstone evaluation and
internal verification; do not remove data, alter estimates or change approval
rules to simplify the display.

### Follow-up: orange connector and concise date controls

**Pending frontend changes, confirmed 2026-10-30.** This follow-up supersedes
the earlier neutral/gray connector instruction and requests removal of the
verbose date-control help. Do not change backend calculations or date handling.

- In `src/features/dashboard/components/admin/ForecastChart.tsx`, render the
  existing `forecast-boundary-connector` in **orange**, matching the past
  prediction line's color, width and dash pattern (`C.orange`, `2.5`, `5 4`).
  The joining segment should visually continue the past series up to the
  current point; the current point and onward remain green. This is a styling
  convention, not an extra historical prediction or a change in forecast origin.
  Preserve the adjacent-bucket checks, zero endpoints, genuine null gaps and
  all prediction values. Do not add a gray connector legend or technical caption.
- Remove the paragraph with `id="forecast-date-help"` beginning **“The To date
  anchors the server's centered window and seasonal outlook.”** It is frontend
  copy, not a backend response. Keep From/To labels, date errors and Apply controls.
- Remove the paragraph with `id="report-date-help"` beginning **“Dates use
  Asia/Manila time and apply to all reports.”** It is also frontend copy. Keep
  the dates, validation, reset button and selected-range display; no replacement
  documentation paragraph is needed.
- Update `aria-describedby` when removing either help element so date inputs
  refer to an existing error element only when an error is present. Preserve
  accessible input labels and validation announcements.
- Remove similar persistent developer explanations around the graph about
  centered buckets, saved horizons, proration, model fitting and display bridges.
  Use dates and short state messages instead, such as **No forecast available
  for these dates** or **Earlier dates are unavailable** where appropriate.
  Do not imply that the From date filters the full longer outlook: label that
  view with its actual returned dates. Preserve current-inventory **As of [date]**
  labels and genuine availability/approval notices.
- Keep API `range_note` and date semantics for internal documentation. Do not
  print its technical explanation verbatim in the client preview; use the short
  status-based wording below. The PDF's existing range explanation is separate
  from these two frontend-only paragraphs.

Verify the connector color/dashes across Weekly, Monthly and Yearly for all
three sales views. Verify both date dialogs no longer show the quoted help,
have no dangling accessibility references, and still send the same valid date
parameters. Date validation, reset, missing values and error states must work.

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

## Product-only inventory cleanup — 2026-10-30

**Backend and frontend implemented (`63ecff33`).** Updated frontend inventory,
outflows, notifications, stock/batch components, settings labels and report
types use products only. The requirements below remain the contract for older
clients. Supplier and
ingredient management are retired. Inventory consists of products, product
batches, categories, stock movements/counts and FEFO settings. Milk feeding is
a sales group of products and remains supported; it is not ingredient inventory.

- Migration `inventory.0012_product_only_inventory` removes `Ingredient`,
  `IngredientBatch`, `Supplier` and the ingredient references on stock movements.
  Stock adjustments/counts now require a non-null `product_batch`.
- Existing product requests and stock logic remain. Send `product_batch_id` for
  adjustments/counts; a supplied non-null `ingredient_batch_id` returns HTTP 400.
  Movement responses no longer contain `ingredient_batch`.
- To avoid breaking older clients' parallel inventory/notification requests,
  legacy collection GETs for `/inventory/ingredients/`, `ingredient-batches/`,
  `suppliers/`, `low-stock/ingredients/` and `expiring/ingredients/` return `[]`.
  They have no database models or management functionality. Legacy detail and
  write requests return HTTP 410 with a removal message. Authentication remains
  required. These compatibility routes are not advertised by the inventory API
  root and should no longer be called by updated clients.
- Reports and notifications use products only. `total_ingredients: 0` remains
  solely as a deprecated JSON compatibility field; report items have
  `item_type: "product"`. The inventory PDF no longer includes an Ingredients card.
- Frontend owner: remove ingredient/supplier DTOs and service methods, parallel
  ingredient requests in `inventory.service.ts`, `outflows.service.ts` and
  `notifications.service.ts`, ingredient fallbacks in stock/batch components,
  and ingredient labels in inventory/settings/report types. In particular,
  outflows must load product batches only. Do not remove product outflows,
  expiry alerts, milk-feeding products or product/category access controls.

Deployment: back up the database, stop API/forecast workers, run the migration,
invalidate business caches and restart all workers with the rebuilt backend
image. The migration refuses to proceed if any retired records or movements
without a product batch exist. Explicitly archive those records before retrying;
there is no automatic deletion/conversion of legacy stock. Historical migration
files stay intact so existing installations and empty Docker databases both
have a valid migration path. Existing Docker exports need a fresh image/export
to include this cleanup.

## Current completion checklist

| Requirement | Status in frontend `49c3cb42` |
| --- | --- |
| Registration length limits and Unicode handling | Complete |
| Selected dates on all six report previews and PDF exports | Complete |
| Historical receipt amount labels and preserved source values | Complete |
| Mutation cache invalidation and manual report-refresh handling | Complete; respects separate preview/PDF cooldowns |
| Adjacent past/current chart connector | Joining logic complete; pending orange styling to match the past series, with genuine null gaps retained |
| Historical date controls on the dashboard forecast chart | Complete |
| Per-customer lifetime total (`total_spent`) | Partial; table/profile/mobile displays implemented, but an open profile retains an old total after the list refreshes |
| Server retry UUIDs for checkout/orders | Pending; existing unconfirmed-sale guard remains, but callers still send no UUID |
| Explicit 52-week/12-month Seasonal outlook display | Complete; covered for all three sales views |
| Latest outlook labels, origin/horizon tooltips and separate issued comparisons | Complete; zero-valued original issues remain separate |
| Forecast diagnostics for research/verification | Graph metadata available; strict monthly evidence remains available from the backend. Showing technical approval details to the ordinary client is no longer requested |
| Plain-language client forecasting and report summaries | Pending; simplify technical panels, labels and report text, and remove the verbose forecast/report date-help paragraphs as requested above |
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
manually encoded and imported receipts once each, using the recorded final invoice amounts. It is independent of purchase-history pagination.
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
  product and product-batch references. Decimal IDs such as `1.9` are
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
4. The `ReferenceLine` connector runs from the last past point into the current
   point. Follow the latest orange-styling request above; its past endpoint is
   not a future prediction. Preserve
   true null gaps and all numeric values. Do not extend the latest-origin model
   backward through historical actuals to create a continuous-looking line.
5. Replace generic “SARIMA estimates” wording where selected components are
   AR(1)/local-level. Use the returned readiness/model details and approval state.
6. Test all nine views, zero-valued issues, unavailable components, missing closed
   months, January/December annual boundaries, same-origin current/future points,
   immutable issue comparison, horizon labels, and the orange connector. Do not
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
completed connector implementation below to the latest orange styling above.

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

## Connector joining logic implemented; orange styling pending

**Gray connector implemented in frontend `49c3cb42`; orange requested next.**
The joining logic is present. Change its styling to match the orange past line
and remove the technical bridge caption, as requested above. The backend returns consecutive calendar
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
3. Render that segment as a dashed orange `ReferenceLine`, which is supported by
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
  stroke={C.orange}
  strokeWidth={2.5}
  strokeDasharray="5 4"
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
Keep this display/provenance explanation in developer documentation rather than
adding another client-facing caption.

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
  Preserve `range_note` in the response; use short client availability messages
  rather than printing its technical explanation verbatim.

### Frontend implementation requested

1. In `src/features/reports/utils/reportDateRange.ts`, include `inventory` and
   `sarima_forecast` in date-range support and validation.
2. In `AdminReports.tsx`, forward the selected range to **all six** preview hooks
   and PDF downloads. Remove the exceptions that discard it for these two cards.
3. Show the server-returned date range on both cards. Inventory descriptions
   should identify stock movements; label its health figures as current with
   `as_of`. Forecast descriptions should identify available predictions for the
   selected dates and show short availability messages without implying a new
   fit was made. Keep technical `range_note` details in documentation.
4. Update `ReportDateRangeControl.tsx`: the range applies to all reports. Remove
   the verbose date-help paragraph per the latest wording revision. Keep current
   health timestamps on inventory figures; date semantics belong in documentation.
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

The recorded-amount distinction remains relevant in
`src/features/sales/utils/receiptDetails.ts`: imported receipts use
`source_line_total` and should label it **Recorded line amount**. Historical
line and invoice amounts may differ; do not infer an allocation or change
stored values. Preserve `source_reference`, `source_line_total`, `subtotal`
and `total_amount`, and treat missing values as unknown.

The newer **Discount removal from the system** request above supersedes the
previous instruction to show discount amounts/explanations. Remove all such
UI rows and text; discount fields are no longer returned by the API. Regression
fixtures should include older adjusted invoices, missing source amounts and
grouped lines to verify invoice totals remain exactly as recorded.

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
