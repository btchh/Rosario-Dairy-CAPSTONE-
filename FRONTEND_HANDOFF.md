# Frontend handoff — open requests only

Updated 2026-10-31 (Asia/Manila), reviewed against frontend `f6e4546a`.

**One request remains: complete server pagination.** Idle logout is now
implemented and deployed with the matching backend and session migration.
The current frontend passed 614 tests, TypeScript and authenticated admin/staff
browser checks, including activity reporting, warning/continuation and expiry.
Completed work and verification are in [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md).
The [previous handoff](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/0d6954ed/FRONTEND_HANDOFF.md)
retains the completed idle-session API contract.

**Work boundary:** implement backend changes here; send frontend changes through
this handoff. Do not edit the sibling frontend source during backend maintenance.
All API paths below are relative to the configured base (`/backend` locally).

## Complete server pagination

**Backend APIs available in the running image; frontend only partly integrated.**

Remaining full-history downloads:

- Staff Sales History (`salesService.getMine`).
- Admin and staff Orders (`ordersService.getAll`).
- Admin Sales History and Sales Encoding when searching or sorting.
- Staff dashboard order counts (`ordersService.getAll`); use an order summary.

### API

Each list returns `{count, next, previous, results}`. Use the full filtered
`count` for pagination.

| List | Summary | Default / maximum page size |
| --- | --- | --- |
| `/sales/transactions/` | `/sales/transactions/summary/` | 50 / 200 |
| `/sales/encoding/` | `/sales/encoding/summary/` | 50 / 200 |
| `/sales/orders/` | `/sales/orders/summary/` | 50 / 500 |

Lists and summaries accept `start_date`, `end_date`, `customer_id`, `handled_by`,
`payment_method=cash|online|unknown`, `payment_status=paid|pending|unknown`, and
`search`. Dates are inclusive `YYYY-MM-DD` bounds in Asia/Manila, using effective
receipt dates; unlinked orders fall back to creation. Search is one literal,
case-insensitive substring up to 200 characters, matching SI/customer/handler;
orders also match an exact numeric order ID. Product names and display-only
placeholders are not searchable by this contract.

Transactions/encoding additionally accept `include_voided=true` (default false).
Orders additionally accept `status=fulfilled|cancelled`; pending is a payment
status, not an order status. Orders have no `include_voided` filter. Payment
filters exclude unlinked orders.

Lists also accept `page`, `page_size`, and one `ordering` field: `id`,
`created_at`, `transaction_date`, `transaction_number`, `total_amount`, `customer`
or `handled_by`; orders also accept `status`. Prefix with `-` for descending.
Default is `-created_at` with stable ID tie-breaking. SI sorting uses the saved
string; `transaction_date` uses the effective receipt date/fallback. Unsupported
filters/sorts must not be assumed to work; invalid documented values return 400.

Transaction/encoding summaries return:
`transaction_count`, `total_revenue`, `paid_count`, `paid_amount`,
`pending_count`, `pending_amount`, `unknown_count`, `unknown_amount`.
They cover all matching recorded totals, including pending/unknown payments;
encoding summaries are restricted to manual receipts. Voids are excluded unless
explicitly included. Money fields are exact two-decimal strings.

Order summaries return `order_count`, `fulfilled_count`, `cancelled_count`,
`pending_payment_count`, `total_revenue`. Revenue covers fulfilled orders linked
to nonvoid receipts. Pending count covers only those orders whose linked
receipt is pending; cancelled, unlinked, voided and standalone sales do not
count. All summaries are independent of page/order parameters.

### Frontend changes and acceptance

1. Use page-specific requests for the remaining paths, including server search,
   sort and filters. Never search/sort only the loaded page as if it were the
   full history. Preserve supported display semantics and adjust search prompts
   to the actual contract; coordinate any additional backend search/sort needs.
2. Use summaries for complete totals/counts. Preserve intentionally lifetime
   or date-only card scopes. Send `handled_by=<current user ID>` for staff
   personal sales/order metrics; staff authentication does not imply own-only
   history. Use `fulfilled_count` for staff fulfilled-order counts and
   `pending_payment_count` for admin pending-order counts. Keep recorded-money
   formatting and show unavailable/loading states when reads fail.
3. Cache by account/session, filters, search, ordering, page and size. Reset to
   page 1 on query changes. On live updates refresh visible pages and summaries;
   recover from a removed last page while preserving filters, selection/drafts.
   Fetch selected details separately if outside the current page.
4. Preserve AbortSignal propagation, account/epoch guards, reconnect/resync and
   existing visibility rules. Use normal cache invalidation; do not add report
   refresh POSTs or automatic business-write retries.
5. Verify initial load, search/sort, summaries, payment updates, cancellations,
   concurrent writes, navigation and reconnect on the representative 4,800+
   receipt dataset. First usable rows/KPIs must not require downloading all
   histories. Record timings, rerun frontend tests/types and rebuild Docker.
