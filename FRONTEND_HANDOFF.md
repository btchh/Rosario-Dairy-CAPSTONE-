# Frontend handoff — open requests only

Updated 2026-11-01 (Asia/Manila), reviewed against frontend `5e390189`.
**One deferred request: complete server pagination.** The user accepted the
current loading cost for finalized release `release-2026.11.01`; this request
does not block that release.

The [backend/frontend parity review](BACKEND_FRONTEND_PARITY.md) separately
documents backend-only and partially exposed capabilities, including Stock
Count. This handoff lists requested implementation work; it is not a claim of
complete feature parity. Keep the pagination APIs and wrappers below.

Frontend `5e390189`, backend `df6f732a` and the stock-out migration are deployed
locally. Completed stock-out/idle-session integration and verification are in
[BACKEND_VALIDATION.md](BACKEND_VALIDATION.md). The completed stock-out API
contract is retained in the
[versioned handoff](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/df6f732a/FRONTEND_HANDOFF.md).

**Work boundary:** backend changes belong here; frontend changes go through this
handoff. Do not edit the sibling frontend source during backend maintenance.
API paths below are relative to the configured base (`/backend` locally).

## Deferred: complete server pagination

Backend APIs are available in the running image. Remaining full-history downloads:

- Staff Sales History (`salesService.getMine`).
- Admin and staff Orders (`ordersService.getAll`).
- Admin Sales History when searching or sorting.
- Sales Encoding for both roles when searching or sorting.
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
