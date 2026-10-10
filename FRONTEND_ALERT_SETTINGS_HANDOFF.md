# Frontend handoff: global inventory alert settings

## Backend prerequisite and contract

Run `python manage.py migrate` in the backend repository. Migration
`0009_remove_item_stock_thresholds` drops the old product/ingredient threshold
columns and saved overrides. Existing global settings are preserved. Newly created
global configuration defaults to **20 stock units**, **7 days for expiration
alerts**, and **3 days for critical expiry**; load saved values rather than assuming
they are still defaults.

Use the existing authenticated HTTP client and trailing slashes:

| Purpose | Endpoint | Access |
| --- | --- | --- |
| Read/change global values | GET, PUT, PATCH `/settings/inventory/` | Admin |
| Settings bootstrap | GET `/settings/` | Admin/staff; `inventory` is admin-only |

For admin bootstrap responses, `inventory` has this shape:

```ts
interface InventorySettings {
  id: number;
  low_stock_threshold: number;
  near_expiry_threshold: number;
  critical_expiry_threshold: number;
  updated_at: string;
}
```

Prefer PATCH with changed fields:

```http
PATCH /settings/inventory/
Content-Type: application/json

{"low_stock_threshold":20,"near_expiry_threshold":7}
```

The existing `/inventory/fefo-config/` configuration uses the same saved values.
There is no individual threshold configuration. Product and ingredient CRUD routes
remain available for other item fields. Their responses, including nested records,
include a **read-only** `low_stock_threshold` containing the global value for
compatibility with existing stock displays. Sending `low_stock_threshold` in any
item create/update request returns **400**, including when the value is `null`.

## Admin Settings work

- Extend `src/features/settings/api/settings.service.ts` with `InventorySettings`,
  the bootstrap field, and `updateInventory` calling PATCH `/settings/inventory/`.
  Represent admin-only bootstrap properties accurately for staff responses.
- Add an “Inventory alerts” section to
  `src/features/settings/pages/AdminSettings.tsx` with “Low-stock threshold” and
  “Expiration alert window (days)”. Explain that these values apply to all products
  and ingredients.
- Include “Critical expiration window (days)” as an advanced field, or show its
  saved value and explain validation. Values must be nonnegative whole numbers,
  with `critical_expiry_threshold <= near_expiry_threshold`. Reducing near-expiry
  below the current critical window requires changing both in the same PATCH.
  Zero is valid. Blank, fractional, negative, nonfinite, and null values are invalid.
- Follow existing loading/retry, dirty-state, saving, toast, and API field-error
  patterns. Preserve unsaved drafts in other sections when saving. Respect
  `permissions.can_manage_settings` and server 401/403 responses.
- Keep notification enable/disable switches separate from the numeric thresholds.
  The switches control alert feeds; thresholds determine which stock/batches qualify.
- Reload saved values and dispatch `SETTINGS_UPDATED` after success. Refresh
  dependent inventory, dashboard, and notification views without a page reload.

## Inventory Management and existing client changes

- Do not expose any individual stock threshold editing controls.
- Continue using the read-only numeric `low_stock_threshold` returned on each item
  when calculating status with `src/features/inventory/utils/stock.ts`, including
  the inventory mapping and `KPICards.tsx`. This is now the same global value for
  every item. Low stock means available stock **at or below** that value.
- Remove product creation's hardcoded `low_stock_threshold: 10` and writable
  threshold properties in `CreateProductPayload`/`CreateIngredientPayload`.
  These frontend changes are still required; this backend task did not implement
  them. Read interfaces (`DjangoProduct`/`DjangoIngredient`) retain the numeric field.
- Product/ingredient create and update payloads must omit the threshold entirely.
  Audit any additional callers/forms for legacy threshold writes.
- Optional item detail text may show the global threshold as read-only with the
  item's unit. Edit global values through Settings only.

## Expiration displays and permissions

Audit hardcoded windows in `AdminInventory.tsx`, `StaffInventory.tsx`,
`src/features/dashboard/components/staff/InventoryAlert.tsx`, and `fefoStatus` in
`src/features/inventory/api/inventory.service.ts` (currently 7/14 days). Admin
alert calculations should use saved configuration or backend alert status.
Batch expiration dates remain batch properties; there is no per-item expiration
alert-window setting.

Backend alert feeds:

- `/inventory/low-stock/products/`
- `/inventory/low-stock/ingredients/`
- `/inventory/expiring/products/`
- `/inventory/expiring/ingredients/`

Expiry feeds include today through today plus the configured near-expiry window,
exclude already expired batches, and annotate `expiry_status` as `critical` or
`near_expiry`. Add optional read types for that annotation where needed. Disabled
notification switches make their corresponding feeds empty; ordinary inventory
status can still show stock and expiry information. Keep expired-batch warnings
separate from near-expiry alerts.

Staff cannot GET the global settings endpoint. Use permitted alert feeds for staff
alerts rather than calling the admin endpoint or inventing another configuration
route. Staff stock displays can use the global value returned with item records.

## Acceptance checks

1. Admin loads saved values, updates global thresholds, and sees validation errors;
   staff settings do not request the admin endpoint.
2. Item creation and editing send no threshold field. Item responses still provide
   the global numeric value; no item-specific controls are shown.
3. Global threshold changes affect all products and ingredients, including records
   that previously had overrides. Test the inclusive boundary and zero.
4. Saved expiry windows, critical-window validation, and notification switches are
   reflected in applicable alerts after refresh. Failed saves preserve drafts.
5. Run `npm test` and `npm run build`; use focused coverage for payload omission,
   global validation, and refreshing dependent alert displays as needed.
