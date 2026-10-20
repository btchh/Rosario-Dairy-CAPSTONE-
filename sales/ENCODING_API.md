# Staff encoding API

Staff and admins can encode a sale dated today or in the past. The API writes
the existing transaction and transaction-item tables, uses the existing manual
item-price/discount calculation, and deducts **current available stock** through
FEFO. A backdated transaction does not recreate stock from its historical date.
Expired/disposed stock remains unavailable, and insufficient stock rejects the
whole entry without leaving an order or partial transaction.

## Create an entry

`POST /sales/encoding/` with the existing bearer authentication.

```json
{
  "request_id": "12640ab5-3b27-40d9-80f8-376bd456fbe9",
  "transaction_date": "2026-10-01",
  "customer_id": 42,
  "si_number": "SI0608",
  "note": "Encoded from the paper invoice",
  "payment_method": "cash",
  "payment_status": "paid",
  "amount_tendered": "400.00",
  "items": [
    {
      "product_id": 373,
      "quantity": "2.00",
      "unit_price": "150.00",
      "discount_type": "none",
      "discount_value": "0.00"
    }
  ]
}
```

The IDs and SI number above are examples. Select actual products and customers.

| Field | Contract |
| --- | --- |
| `request_id` | Required UUID. Generate once per new entry; reuse it for retries. |
| `transaction_date` | Required `YYYY-MM-DD`, today or earlier in the configured business timezone (normally Asia/Manila). Future dates are rejected. |
| `customer_id` | Optional active customer ID; omit or send null for a walk-in. |
| `si_number` | Optional recorded invoice number, at most 50 characters. No number is generated when omitted. |
| `note` | Optional note, at most 2,000 characters. |
| `payment_method` | `cash` (default) or `online`. |
| `payment_status` | `pending` (default) or `paid`. |
| `amount_tendered` | Required for a paid cash transaction and must cover its total. Omit or send null/zero for pending payments. |
| `items` | Same explicit price, quantity and per-line discount contract as checkout/orders. |

Prices are required on every line. Item discounts accept `none`, `fixed` and
`percent`; existing permissions still require an admin for nonzero discounts.
Staff cannot bypass that rule through this endpoint. Whole-invoice discounts
are rejected. Pending transactions still create the sale/order and deduct stock;
their payment can be updated separately using the endpoint below.

The example creates a gross/net total of `300.00`, cash change of `100.00`, and
deducts two units from current stock. For a selected customer, it creates the
usual fulfilled order and order items linked to the same transaction. Walk-in
entries create a transaction without an order, matching direct checkout.

## Response and retry behavior

- HTTP 201: a new transaction was committed.
- HTTP 200: the same user retried the same request and received the original
  transaction; stock, order and revenue are not recorded again.
- HTTP 409: a request ID was reused for different content or by another user.
- HTTP 400: invalid date, price, quantity, discount, cash payment, customer,
  product or insufficient stock. No partial entry is saved.
- Normal authentication, visibility and sale-write throttling remain in effect.

Generate the request ID when starting an entry. Retain it through network
timeouts and retries. Generate a new ID for a genuinely different transaction;
do not regenerate it on every HTTP retry. UUID keys are globally unique.
Concurrent requests sharing a key are serialized before inventory deduction.

The response is the full existing transaction representation, extended with:

| Response field | Meaning |
| --- | --- |
| `transaction_date` | Effective business date as `YYYY-MM-DD`. |
| `created_at` | Effective sales timestamp used by existing history and reporting. Past dates use local noon; today's entries retain current entry time. |
| `encoded_at` | Actual entry timestamp; never backdated. |
| `handled_by` | Authenticated staff/admin who entered the transaction; cannot be supplied by the client. |
| `is_manually_encoded` | True for entries made through this module. |
| `encoding_request_id` | UUID associated with this entry. |
| `transaction_number` | Recorded `si_number`, or null. |
| `source_note` | The entered note. |

These metadata fields are also available in normal transaction/customer history
and transactions nested in order responses. Older transactions have null
`encoded_at`/`encoding_request_id` and `is_manually_encoded=false`. Their dates
and monetary values are not changed by the schema migration.

## Browse entries

```text
GET /sales/encoding/?page=1&page_size=50
GET /sales/encoding/{transaction_id}/
```

These endpoints show manually encoded transactions using existing staff
category-visibility rules. They use the same paginated envelope as transaction
history: `count`, `next`, `previous`, `results`. Page size defaults to 50 and is
capped at 200. Results sort by effective date descending, then transaction ID.

List filters match `/sales/transactions/`: `start_date`, `end_date`, `customer_id`,
`handled_by`, `payment_method`, and `include_voided=true`. Voided entries are
hidden by default. Payment status has a dedicated PATCH endpoint; general transaction editing
and deletion are not supported.
Admin cancellation of a linked order follows the existing order cancellation
API, including its stock-restoration rules.

## Update whether an encoded transaction is paid

`PATCH /sales/encoding/{transaction_id}/payment/` is available to staff and
admins under the same transaction visibility rules as the encoding module.

To record payment in cash:

```json
{
  "payment_status": "paid",
  "payment_method": "cash",
  "amount_tendered": "400.00"
}
```

For an online payment, send `payment_status: "paid"` and
`payment_method: "online"`; no cash tender is required.

To correct the record to unpaid:

```json
{
  "payment_status": "pending"
}
```

- `payment_status` is required. `payment_method` and `amount_tendered` are
  optional when updating, subject to cash validation. Paid cash must have
  sufficient tender. A repeated paid update may retain existing cash details.
- Marking pending clears `amount_tendered` and `change_due`. Pending requests
  with positive tender are rejected; partial payments are not supported.
- Returns HTTP 200 with the full transaction, including `payment_status`,
  `payment_updated_at` and `payment_updated_by` (the updater's user ID).
- An identical update is safe to repeat and retains the previous update time.
- Voided transactions cannot be updated. Nonmanual transactions are unavailable
  through this endpoint. Fields unrelated to payment are rejected.
- This updates the existing transaction only. It does not create another sale,
  deduct stock, change quantities/prices/discounts, or change the effective sale
  date. Existing sales reports and forecast revenue remain based on the sale,
  not its collection status; marking paid does not count the revenue again.
- Status and latest-update metadata appear in transaction history, customer
  history and nested order transactions. Encoding and general transaction lists
  accept `payment_status=pending|paid|unknown` as an optional filter.
- Older records default to `unknown` (“Not recorded”), preserving uncertainty
  about whether historical invoices were paid. Existing nonmanual records are
  not retroactively marked paid. New normal checkout/orders record `paid` as
  before; new encoding defaults to `pending` unless explicitly marked paid.
- Replaying the original create request after a payment change returns the
  current transaction and payment status without another stock deduction.

## System integration

| Existing area | Encoding behavior |
| --- | --- |
| Sales history and date filters | Same transaction ID and receipt, under the selected transaction date. |
| Customer history/count/last sale | Included through the normal customer relationship; last sale follows the effective date, not entry time. |
| Orders | Customer-linked entries create the normal order/transaction relationship. Order date matches transaction date; order `updated_at` retains actual entry time. |
| Inventory and stock alerts | Current FEFO stock is deducted exactly once; standard inventory readers see the committed quantities. |
| Product/category sales summaries | Existing snapshot fields and transaction items feed the same summaries. |
| Daily/weekly/monthly reports and exports | Counted in the period containing the selected date. A past-period sale does not inflate today's sales. |
| Forecasting | Past completed, non-voided sales enter the existing daily revenue source. The source signature changes, so an old evaluation is not presented as current. Existing offline forecast evaluation still needs to run for updated predictions. Today's sales enter training input after the day is complete, as before. |
| Server caches | Standard business-write invalidation runs after commit for history, catalog, customer and report readers. |

## Frontend work

Add a staff **Encoding** navigation entry and page with customer selection,
transaction date, recorded SI number, note, payment inputs, and manual item
price/quantity/discount controls. Show the selected sales date and actual entry
time separately when reviewing a receipt.

Use the returned transaction ID and full response for history/detail navigation.
Refresh relevant screens after a successful entry. Extend the frontend API
client's mutation invalidation for `/sales/encoding/` to include sales, orders,
customers, inventory, dashboard and reports; the frontend currently only
recognizes the older mutation URLs. Maintain the request ID on retries and
disable repeated submission while a request is pending.

Include a Paid/Pending selector on encoding and a payment-update action on its
history/detail view. Show `unknown` as “Not recorded” for older records. Treat
payment method, fulfillment status and payment status as separate fields.
After a successful payment PATCH, invalidate the same sales/customer/order
caches used after encoding so badges and receipt details refresh together.

The backend is implemented; no frontend page or navigation was changed.

## Deployment

Apply `python manage.py migrate` for
`sales/0012_transaction_encoded_at_and_more` before serving the updated code.
Payment updates additionally require `sales/0013_transaction_payment_status_and_more`.

Verification: 454 backend tests passed, including payment transitions,
cross-module history/status consistency, request retries after payment changes,
stock/revenue preservation, authorization and concurrent encoding retries.
