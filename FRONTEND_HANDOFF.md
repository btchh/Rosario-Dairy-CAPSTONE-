# Frontend handoff — sales encoding and customer history

Updated: October 20, 2026 (Asia/Manila).

The backend changes below are implemented. Frontend changes are limited to
recorded SI-number display and internal sales-table keys; the other frontend
integration work remains. This document excludes database cleanup.

## Inventory records: spoilage, complimentary items and free taste

Use `POST /inventory/stock-adjustments/` to record stock leaving a batch without
a sale. Spoilage already exists; `complimentary` and `free_taste` are new types.
Keep `taste_test` for its original meaning, Taste Test Rejection.

The inventory form needs adjustment type, selected product/ingredient batch,
quantity, inventory unit cost and optional notes. Staff member and recording
time are filled by the backend. Add the new types to inventory history and
support `adjustment_type`, `start_date` and `end_date` filters.

These records reduce inventory and update stock totals/alerts. They do not
create sales/orders or add revenue to reports/forecasting. See
[Inventory outflows API](inventory/OUTFLOWS_API.md) for the payload and validation
rules. Apply `inventory/0011_alter_stockadjustment_adjustment_type`.

## New: staff Encoding module

See [Staff encoding API](sales/ENCODING_API.md) for the new backend module:
`POST /sales/encoding/`, `GET /sales/encoding/`, and
`GET /sales/encoding/{transaction_id}/`.

Staff/admin users can enter a custom sales date, customer, SI number and manual
item prices/quantities. Per the client's choice, encoding past sales deducts
**current stock** through FEFO. Selected customers receive a linked fulfilled
order. These are normal shared transactions, included in customer/sales history,
reports and forecasting input under the selected sales date. `encoded_at`
separately records actual entry time. Existing discount permissions remain.

Add the staff navigation/page and a date picker, retain a UUID `request_id` for
safe retries, and extend frontend cache invalidation to recognize
`/sales/encoding/`. Detailed field, retry and integration rules are in the linked
API document. Apply migration `sales/0012_transaction_encoded_at_and_more`.
Encoding now also supports Paid/Pending status and
`PATCH /sales/encoding/{transaction_id}/payment/`; apply
`sales/0013_transaction_payment_status_and_more` for payment tracking.

## Completed and pending work

| Requirement | Current state |
| --- | --- |
| Remove product selling prices | Removed from the Product model and product API. Update frontend forms, types and mappings. |
| Manual price and quantity encoding | Each item requires an entered selling price on every sale/order request. |
| Per-product discounts | Percentage and fixed discounts implemented per encoded line. |
| Customer transaction history | Customer-specific, paginated endpoint implemented. Wire customer selection to it. |
| SI number | Backend and frontend use the recorded SI number. Missing numbers display “Not recorded”; sales table keys use the numeric transaction ID. |
| “Make total amount editable/remove amount” | Client confirmed this requirement is completed. Current APIs calculate totals from entered item prices, quantities and discounts; there is no arbitrary total override. |
| Pending payment status | Encoding defaults to Pending and staff/admins can update Paid/Pending through the payment endpoint. Frontend controls and badges still need integration. |
| Spoilage, complimentary and free-taste records | Inventory API supports all three as separate stock-adjustment reasons. Add frontend form/history controls. |
| Session-expired notification on logout | Cause reproduced; frontend fix still needed. See below. |

## 1. Products no longer have a selling price

Product responses, including products nested inside batches and orders, no longer
contain `unit_price`. Product creation and update reject a supplied `unit_price`.

Frontend changes:

- Remove the selling-price input and column from product management.
- Remove the price field from product create/update payloads and product types.
- Remove mappings such as `parseFloat(product.unit_price)`; they now produce
  invalid values. Review stock alerts, inventory values and nested product mappings too.
- Do not copy a catalog price, batch cost or previous sale price into a new sale.
  Each new item needs manual price entry; blank input must stay blank until entered.
- Historical sale/order items still have their own recorded `unit_price`.

`ProductBatch.unit_price` still exists as an optional inventory cost. It is not a
selling-price default. An omitted batch cost remains `null`; do not fall back to
`batch.product.unit_price` or silently turn missing costs into zero.
Ingredient prices and ingredient-batch behavior were not changed.

## 2. Manual encoding and item discounts

Use the existing authenticated endpoints:

- `POST /sales/checkout/` for direct sales; customer selection is optional.
- `POST /sales/orders/` for orders; `customer_id` is required.

Paths are relative to the existing API client base URL. Keep using its bearer
authentication and trailing-slash handling.

### Request sample: what the frontend sends

This example demonstrates the JSON structure, not a request to run against the
database. Replace the example IDs with IDs selected by the user. The same item
structure works for both endpoints.

```json
{
  "customer_id": 42,
  "payment_method": "cash",
  "amount_tendered": "2200.00",
  "items": [
    {
      "product_id": 373,
      "quantity": "10.00",
      "unit_price": "150.00",
      "discount_type": "fixed",
      "discount_value": "50.00"
    },
    {
      "product_id": 375,
      "quantity": "5.00",
      "unit_price": "160.00",
      "discount_type": "percent",
      "discount_value": "10.00"
    }
  ]
}
```

| Line | Gross amount | Discount | Net amount |
| --- | ---: | ---: | ---: |
| 10 × 150 | 1,500.00 | 50.00 fixed for the whole line | 1,450.00 |
| 5 × 160 | 800.00 | 10% = 80.00 | 720.00 |
| Total | 2,300.00 | 130.00 | 2,170.00 |

Cash change is `30.00`. Use the server response for saved totals and receipts.
Money calculations use decimal arithmetic and round half up to two places.

### Input rules

- `unit_price` is required for every item, on every request. Missing, null, blank,
  negative and non-finite values are rejected. Explicit zero is allowed.
- Quantity must be at least `0.01`. Quantity and unit price each support at most
  two decimal places and eight digits before the decimal point.
- `discount_type`: `none` (default), `percent`, or `fixed`.
- `discount_value` defaults to zero and supports at most two decimal places.
- Percentage range: 0–100. A fixed discount cannot exceed that line's gross amount.
- A fixed discount applies once to the whole line, not once per unit.
- For `none`, discount value must be zero. Combine duplicate products into one line.
- Existing permissions remain: staff can enter selling prices; nonzero discounts
  require an admin. Hide or disable discount controls for staff accordingly.
- Remove the whole-cart/order discount input. Non-default top-level discounts
  are rejected. Omit top-level `discount_type` and `discount_value` from requests.
- Do not submit calculated `subtotal`, `discount_amount`, or `total_amount`.

Cash still requires sufficient `amount_tendered`. New sales accept payment method
`cash` or `online`; `unknown` appears in imported history. Orders still fulfill
immediately and deduct stock. These behaviors have not changed.

Validation failures return HTTP 400, usually with an `error` string identifying
the zero-based item index and invalid field. Continue handling structured field
errors too, such as customer validation errors.

### Response fields and history compatibility

| Field | Order item | Transaction item |
| --- | --- | --- |
| `unit_price` | Entered price | Entered price |
| `subtotal` | Gross line amount | Gross amount allocated to this batch fragment |
| `discount_type` / `discount_value` | Requested line discount | Discount for this fragment |
| `discount_amount` | Calculated line discount | Allocated discount amount |
| `total_amount` | Net line amount | Net amount for this fragment |
| `line_number` | Not supplied | Original input position, starting at 1 |

FEFO can split a single encoded line across multiple stock batches. Transaction
items sharing `line_number` belong to that encoded line. Sum their subtotals,
discount amounts and totals when displaying one row per encoded product.
For fixed discounts, each fragment's `discount_value` is its allocated share;
do not apply the original fixed discount to every fragment. Percent values
retain the requested percentage. Use returned monetary amounts to avoid
rounding drift when grouping fragments.

Transaction-level `subtotal`, `discount_amount` and `total_amount` remain summary
fields. For new sales, `discount_amount` sums item discounts; top-level
`discount_type` is `none` and `discount_value` is zero. Do not interpret that
`none` as meaning there were no item discounts.

Older records retain their original prices, totals and invoice-level discounts.
New transaction-item `subtotal`, `total_amount` and `line_number` can be `null`
on those records; older order-item `total_amount` can also be `null`.
No historical per-item discount was inferred. Keep displaying historical
invoice discounts. Prefer recorded `source_line_total` for imported line amounts
when present, and do not present a gross fallback as a net amount. An imported
zero price can mean an unrecorded price: check the recorded source amounts/notes.

## 3. Customer selection → transaction history

```text
GET /sales/customers/{customer_id}/transactions/?page=1&page_size=50&include_voided=true
```

- Returns `count`, `next`, `previous`, and `results`.
- Default page size: 50; maximum: 200. Fetch subsequent pages to access all history.
- Sorted newest first by creation time, then transaction ID.
- `results` contains full transaction details, including item prices and discounts.
- `include_voided` defaults to false. Use true to show the full permitted history
  and label voided transactions clearly.
- Existing inactive customers can still have their history retrieved.
- Admin and staff access is supported. Existing staff visibility rules apply;
  a mixed transaction containing hidden/inactive categories is excluded for staff.
- Unknown customer: HTTP 404. No history: a valid empty paginated response.
- Customer summary `transaction_count` excludes voided sales, so it can differ
  from history count when `include_voided=true`.

On customer click/selection, fetch this endpoint and show loading, empty and
retry states. Reset pagination on customer changes. Cancel or ignore stale
requests so one customer's response cannot overwrite another's history.
Scope cached history by customer and clear/invalidate it on session changes
and relevant sales/customer mutations. Display business dates in Asia/Manila.

## 4. SI number display

Transaction responses now include read-only `transaction_number`, using the
recorded invoice exactly as stored, for example `SI0608`.

- Available on checkout responses, transaction history/details, customer history,
  and transactions nested in order list/detail responses.
- Missing recorded number returns `null`; show “Not recorded” or a dash.
- The generated transaction-number fallback has been removed from the frontend.
  Sales lists, mobile cards and receipt details label the recorded number as
  “SI Number”. Do not fabricate an SI when the recorded number is missing.
- Use transaction `id` for React keys and internal identity; invoice numbers may
  be absent or repeat across source records.
- `source_invoice_number` remains available for compatibility. Automatic SI
  generation has not been added. Manual SI entry is now supported through the
  staff Encoding endpoint's `si_number` input.

## 5. Payment status for encoded transactions

Send `payment_status: "pending"` (the default) or `"paid"` when creating an
encoded transaction. Pending cash entries do not require tender; paid cash
requires an amount covering the total. Partial payments are not supported.

Use `PATCH /sales/encoding/{transaction_id}/payment/` with `payment_status` to
update an existing encoded entry. When recording cash payment, include
`amount_tendered`; optional `payment_method` accepts cash or online.
Marking pending clears the recorded tender/change fields.

The response includes `payment_status`, `payment_updated_at`, and
`payment_updated_by` (the authenticated updater's user ID). These fields are
also visible in regular transaction/customer history and nested order
transactions. Older records retain `unknown` (“Not recorded”); no historical
payment status is guessed. Normal new checkout/order sales are marked paid.

Add Paid/Pending controls and badges to the Encoding page and payment details.
Payment changes do not create a new sale, deduct stock, or alter revenue totals
or the sale date. Refresh shared sales/customer/order caches after a successful
PATCH. Encoding and transaction lists support `payment_status` filtering.
Do not derive payment status from payment method or fulfillment status.

See [Staff encoding API](sales/ENCODING_API.md) for request samples and validation.

## 6. Logout: false “session expired” notification

The issue was reproduced using the real frontend API client with a controlled
late response. Logout succeeded, both stored tokens were null, and one later
background 401 still emitted the unauthorized event and expiry message.

Current sequence:

1. `AuthContext.logout()` starts server logout and immediately clears local tokens.
2. Backend logout revokes the account's existing tokens.
3. An in-flight protected request finishes afterward with HTTP 401.
4. The response interceptor treats it as expiry of the current session.
5. `onUnauthorized` sets `sessionExpired`, and `AppShell` shows the toast.

The logout endpoint itself is already excluded from the global expiry handler.
Excluding it alone does not address late responses from other endpoints.

Frontend fix requirements:

- Associate protected requests with the session that started them. Late failures
  from a signed-out or replaced session must not expire the current session.
- Guard both refresh/retry logic and unauthorized notification handling, including
  after asynchronous refresh work. A previous session's 401 must not clear a
  newly logged-in user's tokens.
- Keep the session identity stable through normal access-token refresh; access
  token equality alone is insufficient because legitimate refresh rotates tokens.
- Cancel obsolete requests where useful and avoid expiry toasts for intentional
  logout. Preserve real expiry handling for the active session.
- Keep captured logout credentials working after local token cleanup; never
  restore tokens just to finish signing out.

Regression cases: late 401 after successful logout; delayed refresh completing
after logout; old request failing after a new login; normal active-session
refresh; real expired-session handling; and logout with an expired access token.

## Frontend files to review

Paths below are relative to `SarimaHoesButFrontend/`.

| Area | Files |
| --- | --- |
| Shared request/response types | `src/lib/api.ts`, `src/features/orders/types/order.ts` |
| Product forms, prices and cost fallbacks | `src/features/inventory/pages/AdminInventory.tsx`, `src/features/inventory/api/inventory.service.ts` |
| POS encoding and payloads | `src/features/pos/pages/StaffPOS.tsx`, `src/features/pos/api/checkout.service.ts` |
| Order encoding and mappings | `src/features/orders/components/CreateOrderModal.tsx`, `src/features/orders/api/orders.service.ts` |
| Customer history UI | `src/features/customers/pages/AdminCustomers.tsx`, `src/features/customers/components/MobileCustomerCard.tsx`, `src/features/customers/api/customers.service.ts` |
| SI and receipt totals | `src/features/sales/api/sales.service.ts`, `src/features/sales/utils/receiptDetails.ts`, sales history pages and transaction details |
| Logout/session race | `src/lib/api.ts`, `src/features/auth/context/AuthContext.tsx`, `src/features/auth/api/auth.service.ts`, `src/app/App.tsx` |
| Regression tests | `tests/api-contract.test.cjs`, `tests/frontend-ui.test.cjs` |

## Backend references and verification

- [Manual encoding API notes](sales/README.md).
- [Sale-item input validation](sales/serializers/sale_input_serializer.py).
- [Price and discount calculation](sales/services/item_pricing.py).
- [Checkout and FEFO allocation](sales/services/checkout_service.py).
- [Customer history endpoint](sales/views/customer_views.py).
- [Transaction response fields](sales/serializers/transaction_serializer.py).
- [Sales tests, including manual pricing and encoding](sales/tests.py).
- [Backend test layout and run commands](TESTING.md).

Schema migrations were applied to the local backend database. Other environments
need `python manage.py migrate` before using this version of the code:

- `inventory/0010_remove_product_unit_price`
- `inventory/0011_alter_stockadjustment_adjustment_type`
- `sales/0011_orderitem_discount_amount_orderitem_discount_type_and_more`
- `sales/0012_transaction_encoded_at_and_more`
- `sales/0013_transaction_payment_status_and_more`

Verification: 470 backend tests passed using the test-only MD5 password hasher
to speed up fixtures. Production password hashing configuration was unchanged.
Existing history columns matched before/after schema-migration checks, and the
customer history endpoint was checked against the local database. The logout
race is diagnosed and documented here; no frontend fix has been applied.

---

# Reports API handoff

Updated 2026-10-22 (Asia/Manila).

## Customer report: monthly summary for customers with transactions

The customer PDF now uses the selected sales-date period instead of presenting
only the ten highest-spending customers. With no date parameters it defaults to
**the first day of the current month through today**, using Asia/Manila dates.
The PDF includes customers with transactions in the selected range. For ranges
spanning multiple calendar months it also includes a month-by-month overview and
per-month customer summaries, labeled with readable names such as “October 2026”.
Single-month reports omit those repeated monthly sections. Empty monthly sections,
payment follow-up without pending invoices, and unassigned-sales sections without
transactions are omitted from the PDF.

- `data.customers` includes only accounts with at least one visible, non-voided
  transaction in the selected range, including inactive accounts with purchases.
  Accounts with no transactions in the range are excluded. Zero-value invoices
  still count as transactions. There is no top-ten limit. Rows are grouped by
  customer ID, so duplicate names stay separate. `customer_record_count` is the
  number of included accounts.
- `monthly_summary` includes every calendar month touched by the selected range,
  in chronological order. Empty months have zero totals and `customers: []`.
- Each month's `customers` contains **all customers who purchased in that month**,
  without ranking or truncation. Zero-purchase accounts are excluded from both
  the monthly and full-range customer lists.
- First and last month boundaries are clipped to the requested dates.
- Invoice date (`Transaction.created_at`, including manually backdated dates)
  determines the month, rather than the date an invoice was encoded.
- Voided transactions and transactions outside the range are excluded. Existing
  staff category visibility rules apply to every total and monthly breakdown.
  Staff reports omit customer identities without a visible transaction in the
  selected range, including identities linked exclusively to restricted invoices.
- Transactions with no linked customer, including imported source labels, are
  reported separately in `unassigned_transaction_count` / `unassigned_revenue`.
  They are excluded from customer purchase and monthly customer totals.

## Date-range requests

Existing authenticated endpoints now accept optional `start_date` and `end_date`:

```http
GET /api/reports/preview/?type=customer&start_date=2026-09-01&end_date=2026-09-30
GET /api/reports/export-pdf/?type=customer&start_date=2026-09-01&end_date=2026-09-30
```

A single date uses the same value for both parameters. For multiple months:

```http
GET /api/reports/preview/?type=customer&start_date=2026-08-01&end_date=2026-10-22
```

Use the project's existing authentication headers. Local frontend requests use
`/backend/api/reports/...` through the Vite proxy. The backend URLs above are
relative to Django.

| Report type | Without date parameters | Custom date range |
| --- | --- | --- |
| `customer` | Current month to today | Supported; includes calendar-month summaries |
| `daily_sales` | Today | Supported; returns combined selected-range sales |
| `weekly_sales` | Last seven days including today | Supported; daily buckets span the selected range |
| `monthly_sales` | Current month to today | Supported; weekly buckets are clipped to the range |
| `inventory` | Current stock and today's physical ledger | Not supported; returns HTTP 400 if dates are supplied |
| `sarima_forecast` | Existing evaluated forecast | Not supported; continue using the existing `period` parameter |

Date rules apply identically to preview and PDF export:

- Supply **both** dates, in `YYYY-MM-DD` format, or omit both.
- Dates are inclusive, using the configured business timezone (Asia/Manila).
- Start must not follow end. End must not be later than today.
- For the current month, cap the end at today; for a completed month use its last day.
- Invalid dates, incomplete/reversed/future ranges, or unsupported report types
  return HTTP 400 with serializer field errors, for example:

```json
{"end_date": ["Provide both start_date and end_date."]}
```

Custom sales ranges compare against the immediately preceding interval of the
same length. Default comparison windows remain unchanged. Daily sales responses
now also include `start_date` and `end_date`; use those for range labels rather
than `date` (which is retained as the range's final day). Sales responses also
include `is_custom_range`, `previous_period_start`, and `previous_period_end`.

Preview and PDF use the same report data and range. Backend cache keys include
both dates, scope, and report type. PDF attachment filenames include the effective
range. Range changes must also be part of frontend query/download cache keys.

## Customer JSON contract

Money is serialized as decimal strings. Dates use ISO strings; `last_purchase`
can be null. Example for one account with one purchase:

```json
{
  "report_type": "customer",
  "generated_at": "2026-10-22T05:00:00+08:00",
  "data": {
    "as_of": "2026-10-22",
    "start_date": "2026-09-01",
    "end_date": "2026-09-30",
    "timezone": "Asia/Manila",
    "customer_record_count": 1,
    "customers_with_purchases": 1,
    "transaction_count": 1,
    "total_revenue": "500.00",
    "paid_amount": "0.00",
    "pending_amount": "500.00",
    "unknown_amount": "0.00",
    "pending_customer_count": 1,
    "customers": [{
      "customer_id": 17,
      "customer_name": "School outlet",
      "is_active": true,
      "transaction_count": 1,
      "total_spent": "500.00",
      "paid_amount": "0.00",
      "pending_amount": "500.00",
      "unknown_amount": "0.00",
      "pending_count": 1,
      "last_purchase": "2026-09-15"
    }],
    "monthly_summary": [{
      "month": "2026-09",
      "start_date": "2026-09-01",
      "end_date": "2026-09-30",
      "customer_count": 1,
      "transaction_count": 1,
      "revenue": "500.00",
      "customers": [{
        "customer_id": 17,
        "customer_name": "School outlet",
        "transaction_count": 1,
        "total_spent": "500.00",
        "paid_amount": "0.00",
        "pending_amount": "500.00",
        "unknown_amount": "0.00"
      }]
    }],
    "unassigned_transaction_count": 0,
    "unassigned_revenue": "0.00"
  }
}
```

The example shows the fields to use for the new UI; legacy fields are also returned:

- `total_customers`: number of currently active customer profiles.
- `active_customer_count`: customers who purchased in the last 90 days through
  today; this is **not** the selected-range purchaser count.
- `total_lifetime_value` / `average_lifetime_value`: retained lifetime values
  through today, independent of the selected range. Use `total_revenue` for the
  selected-range total.
- `top_customers`: retained as a compatibility name; now includes every purchaser
  in the selected range, sorted by spending. The new report/UI should use
  `customers` for the customer list.
- `repeat_customer_count`: customers with at least two invoices in the selected range.

`total_revenue` equals the sum of `customers[].total_spent`, and also the sum of
`monthly_summary[].revenue`. `transaction_count` follows the same reconciliation.
Paid + pending + unknown equals invoice total. Counts of distinct customers across
months must not be added to calculate the distinct full-range purchaser count.

Payment fields reflect the **current recorded payment status** of invoices whose
sales dates fall in the range. They are not payment receipts during the range or
historical payment-status snapshots. Partial payments and due dates are not
tracked; pending invoice value should not be labeled aged debt or an exact
partially settled balance. Account names and active flags also reflect current
profile records.

## Frontend integration

Frontend changes must be implemented in JayMark's repository by the frontend
owner. This backend release provides the date-range API; it does not change the
frontend. Add an exported `ReportDateRange` interface (`start_date: string`,
`end_date: string`) and optional range arguments to the existing report service
and preview hook. Forward both dates in preview/PDF request parameters and include
them in query/download cache keys. The intended helper signatures are shown below;
implement those helper extensions before using this example:

```ts
import { reportsService, type ReportDateRange } from "@/features/reports/api/reports.service";
import { useReportPreview } from "@/features/reports/hooks/useReportPreview";

const range: ReportDateRange = {
  start_date: "2026-09-01",
  end_date: "2026-09-30",
};

const preview = await reportsService.fetchReportPreview("customer", undefined, range);
await reportsService.downloadReportPDF("customer", range);

// At component top level:
const report = useReportPreview("customer", undefined, range);
```

An actual date-picker control is left for the frontend integration. Keep a draft
range while editing and commit it after both dates are valid, so the hook does
not send incomplete requests. Use the same applied range for preview and download.
Show the API's effective `start_date` / `end_date`, allow same-day ranges, and
handle 400 errors visibly. Label amounts as selected-period invoice totals.

## Inventory ledger changes in this working tree

The inventory PDF starts with a landscape daily product ledger: description and
unit, calculated beginning quantity, production/received, total stock, outlet
sales, customer allocation columns, ending inventory, and remarks. More than six
customer columns continue onto additional sheets with repeated product/balance
columns. Do not add repeated balances across sheets. Existing stock-health and
FEFO sections follow the ledger.

`data.ledger` contains `date`, `columns: [{key, name}]`, and `rows` with `id`,
`name`, `unit`, `beginning`, `received`, `total`, `outlet`, `allocations` (column-key
map), `ending`, `adjustments`, and `remarks`.

This ledger is today's physical stock movement, not historical stock reporting:

- Receipts are initial quantities of batches **entered today**; there is no
  separate production record.
- Stock deductions use invoice encoding time when present, otherwise sales time.
  This differs from customer/sales reports, which use the effective sales date.
- Beginning = ending + sales + signed adjustments − receipts. It is reconstructed,
  not a saved opening count; unlogged edits and prior-day void restorations cannot
  be reconstructed reliably.
- Physical ending balance includes expired/disposed quantities still on hand.
  Sellable quantity is separately shown in the existing stock register.
- Remarks come from today's adjustment quantity/type/reason and expired/disposed
  balances. There is no separate ledger remarks input.
- Historical reference batches and voided sales are excluded. Staff category
  visibility remains enforced.

No database migrations are needed for these reporting changes.

## Report row limits and manual encoding

Reports now include every matching product, product family, category and customer
instead of truncating those lists at eight or ten. Existing response names such
as `top_products` and `top_customers` remain compatible, but are no longer capped.
Inventory health's priority review includes all matching exceptions, and forecast
PDFs include every returned warning. PDF tables fill the available page space and
continue on subsequent pages with repeated headers; there is no ten-row page size.
The inventory ledger's layout, columns and calculations are unchanged.

Manual sales encoding supports existing products, custom quantities and selling
prices, optional recorded SI number, sales date, customer, payment status and an
invoice-level note. It does not support arbitrary product descriptions or extra
freeform ledger rows. Notes on encoded invoices are saved as `source_note`; they
are not standalone ledger rows. Samples and complimentary items should use Stock
outflows, whose type, quantity and reason appear in ledger remarks.

## Print pagination

PDF section headings stay with their following content. When a section starts a
table, there must be room for its heading, repeated column headers, and at least
one data row; otherwise that section starts on the next page. Long tables still
fill the available space and continue across pages with column headers repeated.
The inventory ledger retains its columns, data, and two-row table header.

## Forecasting reset — 2026-10-22 (supersedes previous forecast implementation)

The old forecasting engine and its derived model-result tables have been backed
up and removed. Existing sales/inventory/report APIs are retained. Migration
`forecasting.0005_replace_legacy_engine_with_pattern_studies` removes only retired
forecast storage and creates versioned `PatternStudy` records; migration 0004 is
still required as part of the upgrade chain. No frontend changes are included.

`GET /api/forecasting/forecast/?period=monthly` remains admin-only and now returns
`status: pending_update`, `stage: pattern_investigation`, an empty `forecast`, empty
`metrics`, and a clear rebuilding message. Do not show retired 2025 accuracy or
interpret a withheld forecast as zero revenue. `accuracy_target_percent: 15.0`
means the research target is **below 15% monthly WAPE for regular sales**; it is
not achieved accuracy, nor an accuracy target for feeding/total sales.

New admin endpoint: `GET /api/forecasting/patterns/`. It returns `status: current`
and `findings` for the current source revision and cutoff, or `refresh_required`
with null findings. Research runs offline with `refresh_forecast_patterns`; the
GET request does not fit or refresh a model. A later sales correction/new completed
sale causes a new revision. `PatternStudy` saves prior revisions rather than
replacing their audit history.

Findings include monthly regular/feeding invoice revenue, every regular customer's
cadence and invoice-size statistics, weekday and month-day distributions,
year-over-year comparisons, lag correlations, and research benchmark results.
`coverage_verified: false` and `eligible_for_publication: false` are explicit:
these are investigations, not predictions. Missing dates are not silently set to
zero. Inactivity descriptions refer to `activity_reference_date` (latest observed
sale), not unknown future months. `benchmarks.selected_test` belongs to the
benchmark selected on earlier validation periods; alternative test scores are
diagnostics. No target accuracy has been established for a production model.

See `forecasting/README.md` for source rules, commands, backup/recovery location,
and the staged learning/retraining/promotion design. Current code automatically
refreshes descriptive studies when scheduled; fitted-model updating and live
prediction scoring are the next implementation phase, not completed features.


## Dairy dataset refresh — 2026-10-22

Backend only. Upgrade with `manage.py migrate` (sales migration 0014 adds the
read-only `source_sales_segment` field). Refresh source history with the new
`refresh_annual_sales` command before refreshing pattern studies. This change
does not alter the inventory ledger's layout or live stock.

The refresh reads every sheet in the three original 2023–2025 XLSX workbooks and
retains approved dairy products only. Per the user's clarification, pastillas
(including PDL codes), Pastiroll, Polvoron, and the other identified non-dairy
items are excluded. Unrecognised product labels are excluded pending review.
The explicit policy is `sales/importers/dairy_policy.json`; new products require
a reviewed policy entry. Products, customers, users, settings, demo data, live
batches and manually encoded sales are preserved. Only source-referenced imported
receipts/order lines are refreshed. Existing source SI numbers and receipt IDs
are retained where the invoice still has dairy content; no SI is fabricated.
Mixed-invoice totals use retained source line amounts and proportional source
adjustments. An invoice-only total spanning dairy and excluded products is held
out with a review record, because its dairy amount cannot be determined.

Transaction responses now include `source_sales_segment`:

- `regular` / `feeding`: reviewed refresh classification, with feeding based on
  the recorded source customer label.
- `unclassified`: the dairy entry remains in ledger/report history but has an
  unresolved customer, date context, or price. `source_note` explains the flags.
- empty: live/legacy entry; forecasting checks its item amounts and product
  policy on each snapshot. Mixed live invoices contribute only approved dairy
  revenue; unknown products or missing line details require review.

The source workbook totals SI03744 at PHP 24,816, followed by an independently
totalled PHP 2,087,137 Toned Milk entry without a customer/SI. The latter is
retained separately as `2025:JANUARY:645`, unclassified, with no guessed customer
or invoice number. The dairy-only SI03744 amount is PHP 21,516 after excluding
its PHP 3,300 pastillas line. Date context is preserved as written/inherited;
contradictory sheet/date contexts are flagged instead of silently corrected.

Pattern findings now include monthly `unclassified_revenue` and
`unclassified_invoices`; data quality includes `unclassified_invoices`,
`unclassified_revenue`, and `excluded_non_dairy_invoices`. Do not combine
unclassified amounts with regular revenue or present the research benchmarks as
validated forecasts. Existing customer/sales reports include retained unclassified
receipts; only the regular/feeding research segments exclude them.

No frontend files were changed or pushed for this work.

Applied refresh results: 4,835 historical dairy receipts, 24,285 retained lines,
4,746 regular / 38 feeding / 51 unclassified receipts. Pastillas/PDL, Pastiroll,
and Polvoron are excluded as instructed. The refreshed January–December 2025
retrospective previous-month benchmark is 11.381% monthly regular-revenue WAPE;
this previously inspected period is research evidence, not an untouched model
acceptance test or published forecast accuracy. The forecast endpoint continues
to withhold predictions. 138 integration tests and live API/PDF smoke checks
passed. Private audit, source evidence and current research exports are under
`backups/dataset-refresh-20261022-applied/` and
`backups/dataset-refresh-20261022-findings/`.


## Refreshed demo sales — 2026-10-22

The 2026 demo generator now uses the refreshed, classified dairy-only history.
Pastillas/PDL, Pastiroll, Polvoron and other excluded products are absent;
unclassified entries and held invoices are not copied into demos. The command
`populate_2026_sales --replace-demo --apply` replaces only reserved references
matching `2026:NNNNNN`; real/manual 2026 sales and current inventory are preserved.
Preview by omitting `--apply`. Each generated receipt's `source_note` explicitly
marks it as demonstration data and identifies its historical source reference.
No historical SI number is copied to a synthetic receipt. Demo records cover the
full demonstration year; they are not forecasts. Forecasting excludes them even
when `source_sales_segment` is regular/feeding. No frontend changes are included.

Applied demo refresh: 1,526 receipts (1,492 regular and 34 feeding), 7,982 lines,
and 168 customer identities. All 4,835 historical dairy receipts and the real
forecasting source revision remain unchanged. Protected-row and backup-hash
checks passed; every generated receipt/order amount reconciles. 88 relevant
integration tests passed. Private verification evidence is in
`backups/demo-refresh-20261022-checks/`; the full pre-demo-refresh database is in
`backups/populate-2026-20261022-153202-168549/`.


## Monthly forecasting model and ongoing learning — 2026-10-22

This supersedes the pattern-only forecasting reset section. Backend migration
`forecasting.0006_monthly_learning_lifecycle` adds retained fitted models,
month-specific source coverage, immutable monthly shadow predictions, revisioned
actual-error scores, and deployment/promotion history. Business sales/inventory
records are not changed by model fitting. The frontend repository is untouched.

A first model has been fitted to the refreshed regular dairy data. Nine
candidates include simple baselines, optimized exponential level/damped trend,
and learned ridge autoregressions using past revenue/calendar features. Select
on six earlier validation months; evaluate at twelve later rolling origins;
refit only on completed history. Model family selection never uses the test
winner. The initial validation winner is previous month, with 11.381% WAPE on
the retrospective 2025 test. This is not a verified live accuracy claim.

New admin read endpoint: `GET /api/forecasting/models/`. It returns:

- `status`, `stage: monthly_model_learning`, `model_id`, `engine_version`,
  `model_is_current`, `source_revision`, `cutoff`, and `trained_through`.
- `selected_candidate`, `artifact_sha256`, all retained `versions`, and candidate
  validation/test metrics (`candidates`). Failed candidates carry reasons.
- `evaluation` with selection basis, validation/test periods, selected test,
  selected baseline, and retrospective gate results.
- `research_forecast` with `date`, `predicted_revenue`, `lower_bound`,
  `upper_bound`, `range_kind`, and `sample_count`. This is explicitly a research
  preview; `research_forecast_is_live` is false. Do not put it on a live forecast
  chart merely because it contains numbers. Its target date may be in the past.
- `quality_gate` with `eligible`, failure `reasons`, missing coverage months,
  and prospective model/baseline metrics from genuinely issued predictions.

`GET /api/forecasting/forecast/?period=monthly` remains compatible and reads
saved results only. It now reports `model_id`, `model_status`, `quality_gate`,
`data_provenance`, and separately named retrospective/prospective monthly metrics.
Statuses after fitting include `stale_data`, `refresh_required`, `research_only`,
`insufficient_history`, and `ready`. When there is no trained version yet, the
existing `pending_update` response is retained. Render an empty `forecast` as
withheld, never as zero sales. Weekly/yearly requests are `unsupported_horizon`;
`available_periods` is monthly after fitting. Do not fabricate daily points or
sum a monthly prediction into an unevaluated yearly forecast.

A `ready` forecast contains one regular-dairy monthly point with start/end dates,
training date and an empirical historical-error range. That range is not a
calibrated probability interval. `regular.predicted_revenue` is populated only
when approved; `bulk.status` remains `not_modelled` and `combined` is empty.
Do not label regular revenue as total revenue or assume feeding equals zero.
The downloadable forecast report uses the same withholding status and regular
revenue label.

The current actual dataset stops at December 2025. The first saved research
point therefore refers to January 2026, **not the current month**. Current live
status is `stale_data`; no forecast is published. All generated 2026 demo records
and unclassified sales remain excluded. Historical coverage has not been
certified and no prospective acceptance record has been invented.

`refresh_forecast_models` performs offline fitting, scoring, first-day shadow
issuance and quality-gated promotion. `watch_forecast_models` repeats maintenance,
reusing unchanged fits, learning from new completed months, and retaining old
versions when corrections arrive. An issued prediction is never overwritten by
new actuals or a later model. Coverage must be explicitly reconciled by an admin;
corrections invalidate old coverage. Production publication additionally needs
recent reviewed prospective error below 15% and no worse than the issued
baseline. See `forecasting/README.md` for gates, commands, worker/timer setup and
safe activation/rollback. No API GET performs training or changes source data.

Implementation verification: 163 integration tests passed, including all new
model lifecycle and existing report/encoding/cache/security checks. Live model,
monthly/weekly forecast, patterns and forecast preview/PDF routes returned HTTP
200 with the appropriate withheld/unsupported statuses. All business rows match
the pre-model database backup. Model 1 is saved; no deployment is approved.
The `rosario-forecast-learning.timer` user service is enabled hourly and its first
maintenance run succeeded, reusing model 1. It depends on this workstation's user
systemd manager; it does not enable linger or run while the machine is off.
Private evaluation artifacts are in `backups/model-build-20261022/`.


## Unlabelled Toned Milk entry: preserve null identity

The user confirmed that `2025:JANUARY:645` (PHP 2,087,137) stays **unclassified**
and remains in the sales ledger and report totals. Its customer is null. Sales
migration `0015_allow_unassigned_source_invoice` permits a genuine nullable SI;
this entry now returns `customer: null`, `source_invoice_number: null`, and
`transaction_number: null`. Do not attach PCC-UPLB/SI03744 or infer feeding.
Customer reports include it in unassigned-sales totals, not a fabricated named
account. It remains excluded from regular/feeding forecasting and dummy sources.
Historical imports/refreshes now preserve missing source SIs as null. Legacy
empty strings are still allowed; existing recorded SI strings are unchanged.
Frontend consumers must treat both null and an empty SI as unrecorded. No
frontend repository changes are included.


## SARIMA is the primary forecast — 2026-10-22

This supersedes the earlier general-model selection. Current engine
`regular-monthly-sarima-v2` selects **only among SARIMA configurations** for the
primary forecast. Nine other methods remain comparison benchmarks. A baseline
can have a better validation score without replacing SARIMA; if SARIMA fails,
the backend withholds its forecast rather than silently changing families.

Saved model **2** is SARIMA(0,1,0)(1,0,0,12), selected on six earlier validation
months (July–December 2024, 9.9104% WAPE). The twelve rolling test months in 2025
give **10.6433% SARIMA WAPE** versus **11.381% previous-month baseline WAPE**.
Both scores are retrospective research. The 11.381% figure previously reported
for model 1 belongs to the baseline; do not display it as SARIMA accuracy.

`GET /api/forecasting/models/` now additionally returns:

- `model_family: "SARIMA"` and `selected_candidate: "sarima_010_100_12"`.
- `sarima.order: [0, 1, 0]`, `sarima.seasonal_order: [1, 0, 0, 12]`, `trend`,
  `converged`, `statsmodels_version`, `optimization_attempts`, and `fit_warnings`.
- Candidate `role: "primary"` for SARIMA and `"comparison"` for other methods.
- `evaluation.selection_basis: "sarima_only_earlier_validation_wape"`.
- Retained `versions` across engines, each explicitly labelled `engine_version`.

The forecast API and downloadable report metadata also include `model_family`;
`method` identifies the fitted SARIMA configuration. Failed SARIMA candidates
retain failure reasons. A checksum-valid baseline artifact cannot be issued or
promoted as SARIMA. Model 1 remains preserved for audit; its issued predictions
cannot count toward this engine's prospective acceptance record.

The saved research point is **January 2026**, PHP 1,389,358.58, with an empirical
historical-error range of PHP 1,047,921.04–1,730,796.12. It is not current or a
calibrated probability interval. Real data still stops in December 2025, so live
status stays `stale_data` and `forecast` remains empty. No demo data bridges the
gap. There are only three annual cycles; early validation fits have 18 months,
so seasonal evidence remains limited despite the retrospective target passing.

The existing hourly maintenance now refits SARIMA parameters and reselects its
configuration when completed real data or corrections arrive. Unchanged input
reuses a saved fit. Publication still requires current data, reviewed coverage,
and twelve genuine prospective reviewed months below 15% and no worse than the
baseline. No source sales, inventory, null identities or frontend files change.
Private pre-change backup and evaluation: `backups/sarima-priority-20261022/`.

2026-10-22 SARIMA priority verification: 170 integration tests and six live API/PDF
checks passed. The hourly service successfully reused SARIMA model 2. Source
sales, stock and the real forecasting revision are preserved; no frontend files
were changed and nothing was pushed.
