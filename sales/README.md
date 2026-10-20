# Manual sale encoding API

For the staff module with custom dates and current-stock deduction, see
[Staff encoding API](ENCODING_API.md). Existing checkout/order behavior below
continues to apply to normal sales.

`POST /sales/checkout/` and `POST /sales/orders/` require an explicit `unit_price`
on every item, on every request. Orders also require `customer_id`.
Products no longer have a catalog selling price. Batch `unit_price` remains an
optional inventory cost and never supplies a selling price.

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

For these example quantities and prices, the line totals are `1450.00` and
`720.00`. The transaction subtotal is `2300.00`, its aggregate `discount_amount`
is `130.00`, the total is `2170.00`, and change is `30.00`.

- `discount_type` accepts `none` (default), `percent`, or `fixed`.
- `discount_value` defaults to zero. Fixed discounts apply to the entire line,
  once; percentage discounts apply to `quantity × unit_price`.
- Values support at most two decimal places. Quantity must be positive; selling
  price may be explicitly zero. Percent discounts range from 0 to 100; a fixed
  discount cannot exceed that line's subtotal.
- Existing authorization remains: staff may encode prices, while nonzero
  discounts require an admin.
- Whole transaction/order discounts are rejected. The old top-level
  `discount_type` and `discount_value` remain read-only history fields; new
  records use `none` and zero. Transaction `discount_amount` sums item discounts.
- Order items and transaction items expose `unit_price`, `subtotal`,
  `discount_type`, `discount_value`, `discount_amount`, and `total_amount`.
- FEFO may split one input into multiple transaction items. `line_number` groups
  them by their original input position. Their rounded subtotals, discounts and
  totals add up to the encoded line. Fixed `discount_value` is allocated across
  batches; percentage `discount_value` stays the requested percentage.
- Historical amounts and invoice discounts are retained. Newly introduced
  `subtotal`/`total_amount` fields are null on old transaction items, and
  `total_amount` is null on old order items. No per-item historical discounts
  are inferred; use existing transaction totals and recorded source amounts.

Both product APIs reject `unit_price` writes. No frontend changes are included;
clients must supply the new sale-item fields before encoding new sales.
