# Category icon backend handoff

Updated October 3, 2026. The backend stores an optional preset icon on each category. Existing categories default to an empty icon, so the frontend can keep its current name-based fallback until it is connected. No frontend files were changed for this task.

## API contract

An authenticated admin can include `icon` when creating a category:

```http
POST /inventory/categories/
Content-Type: application/json

{"name":"Soft Cheese","icon":"cheese"}
```

The same field can be changed or cleared later:

```http
PATCH /inventory/categories/123/
Content-Type: application/json

{"icon":"milk"}
```

Send `{"icon":""}` to return to the automatic name-based icon. Category create, list, detail, and update responses include `icon`. Invalid values return HTTP 400 with an `icon` validation error. Staff can read icons but cannot create or edit categories.

The picker values come from `GET /inventory/categories/icon-options/`. Its response is a list of `{ "value", "label" }` objects, including the empty automatic option. The supported values are `milk`, `cheese`, `butter`, `yogurt`, `ice_cream`, `cream`, and `package`.

## Frontend connection

The admin Add Category and Edit Category forms are in `src/features/inventory/pages/AdminCategories.tsx`. Both should use the same icon picker and send the selected `icon` through `inventoryService.createCategory` or `inventoryService.updateCategory`. The API service and `DjangoCategory` type live in `src/features/inventory/api/inventory.service.ts` and `src/lib/api.ts`. The current `CategoryIcon` component in `src/components/data-display/CategoryIcon.tsx` maps category **names** to icons; have it prefer the stored `icon` value when present, then retain the name-based fallback for old categories. The category display model in `src/features/inventory/types/inventory.ts` also needs the field.

Backend files: `inventory/models/category.py`, `inventory/serializers/category_serializer.py`, `inventory/views/category_views.py`, and migration `inventory/migrations/0007_category_icon.py`. The migration has been applied locally. Verification: four `CategoryIconAPITests` pass and `manage.py check` reports no issues.
