# Frontend handoff — open requests only

Updated 2026-10-31 (Asia/Manila), reviewed against frontend `b479b67f`.
**Two open requests: stock-out request IDs and server pagination.**
Verification and completed work are recorded in [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md).

**Work boundary:** backend changes belong here; frontend changes go through this
handoff. Do not edit the sibling frontend source during backend maintenance.
API paths below are relative to the configured base (`/backend` locally).

## 1. Send saved request IDs with stock outflows

Durable pending storage is already implemented in `b479b67f`. The remaining
work is sending/reusing its UUID and supporting confirmed replay. Cover both
`outflowsService.create` and `inventoryService.disposeExpiredBatch`; both
currently POST without a request ID.

**Deployment dependency:** rebuild the backend and apply
`inventory.0013_stock_adjustment_submission` before enabling keyed retries.
The running Docker backend still ignores these IDs.

### API contract

`POST /inventory/stock-adjustments/` accepts UUID `request_id` in the existing
JSON body (recommended), or an `Idempotency-Key` header. If both are supplied
they must identify the same UUID. Invalid/empty/null IDs or disagreement return
400. The header is allowed by CORS for configured frontend origins.

| Result | HTTP status | Behavior |
| --- | --- | --- |
| First success | 201 | Standard stock-adjustment JSON; stock deducted once. |
| Same account, ID and effective payload | 200 | Original saved JSON; no additional deduction or adjustment. |
| Same account and ID, changed details | 409 | `detail`: `This request ID was already used for a different stock adjustment.` |

Compared fields are batch ID, adjustment type, quantity, unit cost and reason.
Equivalent numeric representations match; omitted/null/empty reasons match.
Replay works across restarts, simultaneous retries and full-batch disposal.
Failed transactions roll back stock, adjustment and request record together.
IDs are scoped to the authenticated account and endpoint, without automatic
expiry. Current authentication/visibility rules still apply. Unkeyed requests
remain compatible but have no duplicate-request protection.

### Frontend changes and acceptance

1. Send the durably saved UUID as `request_id` with its exact saved payload.
   Preserve the storage-failure guard and prevent overlapping independent
   submissions. Keep recovery scoped to the originating account.
2. Recover uncertain keyed requests with the same UUID, payload and account;
   accept both 200 and 201. On 409 retain recovery state and reconcile History.
   Never generate a new ID to retry an uncertain write. A failed recovery call
   does not prove the earlier attempt failed. Legacy uncertain requests sent
   without an ID still require History reconciliation; do not assign one later
   and replay them.
3. Refetch inventory and outflow History after confirmed success. Replayed
   nested batch/product values are original snapshots and must not overwrite
   newer stock totals as if they were current reads.
4. Verify lost response/reload, replay, changed-payload conflict, full disposal,
   account switching, concurrent tabs and blocked storage. Do not enable retries
   against the older backend, which silently ignores request IDs.

## 2. Complete server pagination

Backend APIs are available in the running image. Remaining full-history downloads:

- Staff Sales History (`salesService.getMine`).
- Admin and staff Orders (`ordersService.getAll`).
- Admin Sales History and Sales Encoding when searching or sorting.
- Staff dashboard order counts (`ordersService.getAll`); use the order summary.

### API contract

Lists return `{count, next, previous, results}`; use the full filtered `count`.

| List | Summary | Default / maximum page size |
| --- | --- | --- |
| `/sales/transactions/` | `/sales/transactions/summary/` | 50 / 200 |
| `/sales/encoding/` | `/sales/encoding/summary/` | 50 / 200 |
| `/sales/orders/` | `/sales/orders/summary/` | 50 / 500 |

Lists/summaries accept `start_date`, `end_date`, `customer_id`, `handled_by`,
`payment_method=cash|online|unknown`, `payment_status=paid|pending|unknown`, and
`search`. Dates are inclusive `YYYY-MM-DD` bounds in Asia/Manila using effective
receipt dates; unlinked orders fall back to creation. Search is one literal,
case-insensitive substring up to 200 characters matching SI/customer/handler;
orders also match exact numeric order ID. Product names and display-only
placeholders are not searchable by this contract.

Transactions/encoding accept `include_voided=true` (default false). Orders accept
`status=fulfilled|cancelled`, have no `include_voided`, and payment filters exclude
unlinked orders. Pending is a payment status, not an order status.

Lists accept `page`, `page_size` and one `ordering`: `id`, `created_at`,
`transaction_date`, `transaction_number`, `total_amount`, `customer`, `handled_by`;
orders also accept `status`. Prefix `-` for descending. Default `-created_at`
includes stable ID tie-breaking. SI ordering uses the saved string; sales date
uses the effective receipt date/fallback. Invalid documented values return 400.

Transaction/encoding summaries return `transaction_count`, `total_revenue`,
`paid_count`, `paid_amount`, `pending_count`, `pending_amount`, `unknown_count`,
`unknown_amount`. They include matching pending/unknown totals; encoding is
manual receipts only. Voids are excluded unless requested. Money is exact
two-decimal strings.

Order summaries return `order_count`, `fulfilled_count`, `cancelled_count`,
`pending_payment_count`, `total_revenue`. Counts include matching unlinked orders;
revenue covers fulfilled orders linked to nonvoid receipts. Pending count covers
only fulfilled orders linked to nonvoid pending receipts. Summaries ignore paging
and ordering parameters and cover the entire filtered result.

### Frontend changes and acceptance

1. Use server pages/search/sort for the paths above, summaries for complete
   totals/counts, and separate detail reads for selected records outside the page.
   Preserve lifetime/date-only card scopes and recorded-money formatting. Send
   `handled_by=<current user ID>` for staff personal metrics; staff access is not
   own-only. Use `fulfilled_count` and `pending_payment_count` for their respective
   dashboard cards. Show loading/unavailable states when reads fail.
2. Cache by account/session, filters, search, ordering, page and size. Reset to
   page 1 on query changes. Live updates refresh visible pages and summaries;
   recover from a removed last page while preserving filters, selections/drafts.
   Preserve AbortSignals, account/epoch guards, reconnect/resync and visibility.
   Do not add report-refresh POSTs or automatic sales/order write retries.
3. Verify search/sort, summaries, payment updates, cancellation, concurrent writes,
   navigation/reconnect and first usable rows/KPIs on the representative 4,800+
   receipt dataset without downloading full histories. Record timings, run
   frontend tests/TypeScript and rebuild Docker.
