# Frontend handoff — open requests only

Updated 2026-10-31 (Asia/Manila), reviewed against frontend `7295f413`.

Two requests remain. The current frontend is deployed locally; its 580 tests,
TypeScript checks and admin/staff browser smoke checks passed. Completed work
and verification are recorded in [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md).
The [previous handoff](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/7a75a0ab/FRONTEND_HANDOFF.md)
retains historical API contracts and completed requests.

**Work boundary:** implement backend changes here; send frontend changes through
this handoff. Do not edit the sibling frontend source during backend maintenance.
All API paths below are relative to the configured base (`/backend` locally).

## 1. Integrate idle logout

**Backend source ready; frontend missing.** Use a 15-minute idle timeout and
one-minute warning, reading the effective policy from the server.

**Deploy together:** frontend integration, backend migration
`accounts.0005_loginsession`, and the session-enforcing backend image. Existing
JWTs require a fresh sign-in. The running backend is deliberately unchanged:
activating the new backend alone would expire even actively used browsers,
because the current frontend sends no activity reports.

### API

| Endpoint | Behavior |
| --- | --- |
| `POST /accounts/login/` | Existing tokens plus nested `session`. |
| `POST /accounts/refresh/` | Rotated tokens plus nested `session`; does not extend idle expiry. |
| `GET /accounts/session/` | Returns session directly; does not extend expiry. |
| `POST /accounts/session/activity/` with `{}` | Returns session directly; extends only a still-valid session using server time. |

Session shape:

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

Session GET/activity require bearer authentication and return `no-store`.
Activity is limited to 30 requests/account/minute. Client timestamps and session
IDs cannot choose or revive a session. Idle expiry returns **401** with
`code: "session_idle_timeout"`; legacy/missing/invalid sessions return **401**
with `code: "session_required"`. Both include a `detail` message. Database
outages retain the existing **503** behavior.

### Frontend changes

1. Add session types/service methods and restore status on an existing sign-in.
   Keep session-generation guards; discard responses from a prior account or
   sign-in. Use server time/deadlines and retain the latest confirmed deadline
   for the same session when responses arrive out of order.
2. Report trusted pointer/touch, keyboard and scroll input while visible.
   Coalesce ongoing activity roughly every 30 seconds with a final debounced
   report. Coordinate same-session tabs. Never send an unconditional keepalive:
   polling, token refresh, live events, focus, page loading and reconnect alone
   must not extend the deadline. Respect activity 429/Retry-After responses.
3. Show a countdown with **Stay signed in** and **Sign out** when the warning
   begins. Stay signed in posts activity and dismisses only after success.
   Keep drafts during the warning. Network errors must not invent a deadline.
4. On expiry, clear credentials, private caches, drafts and live subscriptions,
   then return to login with **You were signed out due to inactivity. Please
   sign in again.** Handle suspended tabs, sleep/resume and offline expiry;
   block business submissions after the confirmed deadline.
5. Treat `session_idle_timeout`/`session_required` as terminal 401s in
   `src/lib/api.ts`, including refresh failures: do not refresh or retry writes.
   Preserve ordinary access-expiry refresh and temporary-outage behavior.
   Live `auth_required` must use the same session cleanup.
6. Synchronize deadlines/expiry across tabs by session ID without affecting a
   newer or independent sign-in. Automatic idle cleanup must not call the
   existing account-wide logout endpoint. An explicit Sign out may still do so.

**Acceptance:** both roles stay signed in with real activity and expire without
input despite background polling. Verify warning/continuation, multiple tabs,
an independent active device, open editors, stale responses, account switching,
refresh, sleep/resume, offline expiry and rollout re-login. Expired sessions
cannot be revived. Rebuild and verify the coordinated images before release.

## 2. Complete server pagination

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
