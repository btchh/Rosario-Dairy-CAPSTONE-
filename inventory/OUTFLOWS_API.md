# Inventory records: spoilage, complimentary items and free taste

Staff and admins use the existing stock-adjustment API to record inventory
leaving a batch without a sale. These entries do not create transactions,
orders, SI numbers or payment records, and do not contribute sales revenue to
reports or forecasting.

## Record an outflow

`POST /inventory/stock-adjustments/` with existing bearer authentication.

| `adjustment_type` | Meaning |
| --- | --- |
| `spoilage` | Inventory lost because it spoiled; already supported. |
| `complimentary` | Items given away without charge; newly supported. |
| `free_taste` | Samples used for free tastings; newly supported. |

The existing `taste_test` type still means **Taste Test Rejection**. Do not use
it for promotional samples. Other existing adjustment types remain available.

```json
{
  "product_batch_id": 123,
  "adjustment_type": "free_taste",
  "quantity": "2.00",
  "unit_cost": "25.00",
  "reason": "Samples for today's tasting event"
}
```

Use an actual selected batch ID. To record an ingredient outflow, supply
`ingredient_batch_id` instead of `product_batch_id`. Exactly one batch is
required. The API deducts from that specific batch, using its stock unit.

- `quantity` must be positive, have at most two decimal places, and not exceed
  remaining stock. Stock cannot go negative.
- `unit_cost` is required, nonnegative, and supports two decimal places. It is
  the inventory cost per unit, not a selling price. Do not read a nonexistent
  product selling price or silently replace an unknown cost with zero.
- `reason` is optional; use it for event details, recipient information or the
  explanation for spoilage.
- The server records the authenticated staff member as `adjusted_by` and stamps
  the actual recording time. Client-supplied staff/date fields do not replace it.
- Existing expired/disposed batch restrictions remain. Complimentary items and
  free tastings also reject batches whose expiration date has passed, even if
  their status is still `available`.

HTTP 201 returns the normal stock-adjustment representation: ID, selected batch
details, adjustment type, quantity, unit cost, reason, staff and timestamps.
HTTP 400 means invalid input or unavailable/insufficient stock; no partial
deduction or history entry is saved. Existing staff product/category visibility
and authentication apply.

## Stock effects

Each successful record reduces the selected batch's remaining quantity. Partial
deductions retain its normal stock status. Exhausting a batch marks it
`disposed` for spoilage, or `depleted` for complimentary items/free taste.
Normal inventory totals, stock alerts and inventory reports see the new stock
levels through the existing business-write cache invalidation.

These are inventory records, separate from sales encoding. A complimentary item
or tasting must not be encoded as a paid sale or an order with a 100% discount.

## Browse records

```text
GET /inventory/stock-adjustments/
GET /inventory/stock-adjustments/?adjustment_type=spoilage
GET /inventory/stock-adjustments/?adjustment_type=complimentary
GET /inventory/stock-adjustments/?adjustment_type=free_taste&start_date=2026-10-01&end_date=2026-10-20
GET /inventory/stock-adjustments/{id}/
```

Lists return the existing JSON array, ordered by recording timestamp and ID,
newest first. Date filters are inclusive business dates, in `YYYY-MM-DD` format.
An invalid type, malformed date or reversed date range returns HTTP 400.
Recorded adjustments cannot be edited or deleted through this API.

## Frontend integration

Add an inventory record form with type, batch, quantity, unit cost and notes.
Offer distinct labels **Spoilage**, **Complimentary**, and **Free Taste** and
show these records in inventory adjustment history with type/date filters.
Display the staff member and recording date in history. After a successful
POST, refresh stock quantities, inventory history and stock alerts using the
existing inventory cache invalidation flow. No frontend changes are included
in this backend extension.

Apply `inventory/0011_alter_stockadjustment_adjustment_type` with
`python manage.py migrate` before serving the updated code.
