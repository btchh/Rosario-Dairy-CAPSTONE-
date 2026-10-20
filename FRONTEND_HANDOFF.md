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
