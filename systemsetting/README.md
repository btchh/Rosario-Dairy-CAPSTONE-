# Inventory alert settings

Authenticated admins can GET, PUT, or PATCH `/settings/inventory/`. These settings
also appear under `inventory` in the admin response from GET `/settings/`.

```http
PATCH /settings/inventory/
Content-Type: application/json

{"low_stock_threshold":20,"near_expiry_threshold":7}
```

The defaults are 20 stock units and 7 days before expiration. Every product and
ingredient uses the global low-stock threshold; there are no item overrides.
Stock at or below the threshold qualifies as low stock. Global values must be
nonnegative integers. `critical_expiry_threshold` defaults to 3 days and must not
exceed `near_expiry_threshold`.

Product and ingredient responses expose `low_stock_threshold` as a read-only copy
of the global value for existing clients. Sending it in an item create or update
request returns HTTP 400; configure it only through the global settings endpoint.
Product and ingredient CRUD endpoints remain available for their other fields.

These settings share storage with `/inventory/fefo-config/`. Existing saved global
settings are preserved. Migration `0009_remove_item_stock_thresholds` removes the
old per-item threshold columns and their saved values.

Run `python manage.py migrate` before using the updated API.
