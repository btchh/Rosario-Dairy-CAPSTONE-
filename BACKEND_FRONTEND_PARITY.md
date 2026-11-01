# Backend/frontend parity review — 2026-11-01

**Parity is partial. Backend capability does not imply a usable frontend flow.**
This review documents code that the current frontend does not call, exposes only
partly, or uses indirectly. It does not remove code or add features to the release.
**Keep server-side pagination, summaries, filters and detail endpoints.**

## Reviewed revisions and method

- Backend checkout: `0b7f9aee39c23f97d27152e38055ca61f8154bdc`;
  application source remains `df6f732a`.
- Frontend/deployed build: `5e39018993a55461e48b30dedc4ff24132be8c38`.
- Frozen application release: `release-2026.11.01`.
- Enumerated Django/DRF routes and supported method bindings, including inherited
  actions. Traced frontend HTTP calls, service member references and runtime
  imports from `src/main.tsx`. Reviewed payload builders, controls and role layouts.
- Excluded Django admin internals, format-suffix duplicates and automatic
  HEAD/OPTIONS. Tests and type-only references were not counted as UI callers.
- Dynamic batch-detail, event-stream and forecast-job URLs were resolved against
  their implementation; shared HTTP transport calls were traced through callers.
- This is a **static source audit**, not another browser acceptance run or an
  exhaustive dead-function analysis. No business writes, emails or Docker changes.

Evidence: [validation/frontend-parity-20261101.json](validation/frontend-parity-20261101.json).
It lists every reviewed route/method, frontend call locations, unused service
members and 21 custom management commands. Frontend paths below are relative
to the sibling `SarimaHoesButFrontend` repository; backend links are local.

| Declared method bindings | Count |
| --- | ---: |
| Has a referenced frontend request path | 73 |
| Service wrapper exists, no source caller | 4 |
| Business feature has no frontend flow | 6 |
| Standalone forecast diagnostics have no frontend caller | 5 |
| Alternative/equivalent detail, update or configuration route | 24 |
| Restricted batch-deletion maintenance route | 1 |
| Retired inventory compatibility bindings | 5 |
| Retired forecast endpoint returning 410 | 1 |
| Job-detail POST explicitly rejected with 405 | 1 |
| DRF API-root metadata | 2 |
| **Total across 79 route patterns** | **122** |

The legacy inventory regex represents five path families. The job-detail POST
is a declared handler but always rejects writes there. All explicit frontend
request paths resolved to backend routes. That does not establish payload,
permission, field or workflow parity: several gaps below share a called route.

## Business capabilities missing or limited in the UI

| ID | Backend capability | Actual frontend coverage | Consequence / disposition |
| --- | --- | --- | --- |
| P01 | Stock counts: GET/POST `/inventory/stock-counts/`, GET detail; [view](inventory/views/stockcount_views.py), [reconciliation](inventory/services/adjustment_service.py) | No stock-count DTO/service/form/history. Inventory screens use products, batches and adjustments. | **Backend-only physical stocktaking.** No UI entry of counted quantity, expected-versus-actual variance, counter/date/notes or linked correction. Keep the model/API; frontend integration needs a separate request. |
| P02 | Eight [stock-adjustment types](inventory/models/stockadjustment.py) | `outflows.service.ts:6` and `OUTFLOW_TYPES` offer `expired`, `spoilage`, `complimentary`, `free_taste`. | `spillage`, `taste_test` (Taste Test Rejection), `processing_use` and `correction` cannot be submitted by current controls. Free Taste is already supported and differs from rejection. History can display other saved types via its fallback label, but cannot specifically select them in its type filter. |
| P03 | Admin receipt voiding: POST `/sales/transactions/{id}/cancel/` and inherited `/sales/encoding/{id}/cancel/`; [view](sales/views/transaction_views.py), [stock restoration](sales/services/order_service.py) | `ordersService.cancelOrder` is wired in Admin Orders. There is no transaction/encoding cancellation service or button. | **Standalone POS and manually encoded receipts cannot be voided through their UI.** Order cancellation remains implemented. Imported receipts are intentionally non-cancellable. Backend optional cancellation `reason` is not collected by the existing order dialog. Receipt details show the voided flag, but not `voided_at`, `voided_by` or `void_reason`. |
| P04 | Product reactivation: POST `/inventory/products/{id}/reactivate/`; [mixin](inventory/views/mixins.py) | Admin Inventory can delete; linked products become inactive. No product reactivation service/control or inactive-product recovery list. | A deactivated product cannot be restored through the current UI. Categories and customers **do** have reactivation flows. Category management reads inactive products for category counts, not product recovery. |
| P05 | [Product](inventory/serializers/product_serializer.py), [category](inventory/serializers/category_serializer.py) and [batch](inventory/serializers/productbatch_serializer.py) metadata editing | Product creation fixes `variant=null`, `unit="unit"`, `shelf_life=30`; edits send name/category. Category description is created blank and not edited. Batch editing sends cost/notes for expired-batch rows only. | Product variant/unit/shelf life, category description, batch grade/receipt-date edits and existing-batch expiry edits are not offered. Expiry is entered on new stock. Active-batch cost/notes editing has no general batch-management flow. These are **partial field coverage**, not wholly missing inventory CRUD. Batch quantities/status are read-only through batch PATCH; stock changes require their dedicated flows. |
| P06 | Self-service profile PATCH accepts `phone_number` and `address`; [profile service](accounts/services/profile_service.py) | Personal Profile and `userService.updateCurrentUser` cover username, names and verified recovery email. | Users cannot edit their own contact number/address in Personal Profile. Admin User Management already creates/updates these fields for users, so the backend fields are not globally unused. |
| P07 | Staff may preview/export five non-forecast report types and request report refresh; [report views](reporting/views.py), [role guard](reporting/services.py) | Report services are wired to Admin Reports. `StaffLayout.tsx` has no Reports page or report-download control. | Staff report capability is **API-only for that role**. Forecast reports remain admin-only. Whether staff should receive a report screen is a product/role decision, not an automatic permission change. |
| P08 | System settings accept `system_name`, `currency`, `date_format`, `language`; [serializer](systemsetting/serializers/settings_serializers.py) | Admin Settings edits six business-profile fields, three inventory thresholds and three alert switches. It does not offer these general-preference controls. | Values are returned in the overview but not editable through this UI. Currency affects backend PDFs while UI money formats remain PHP; language affects server middleware while UI strings are fixed. Do not promise global currency/language/date-format switching. Timezone is governed by `DJANGO_TIME_ZONE` and the API rejects a different zone; it is not a missing arbitrary-timezone selector. |
| P09 | Notification fields `forecast_warnings`, `report_ready_notifications`; [model](systemsetting/models/notification_settings.py) | Present in the frontend type, absent from Admin Settings' `liveAlerts` controls. | These are **reserved/inactive flags**, not two complete backend alert features waiting for buttons: [the inbox generator](systemsetting/notifications.py) generates stock, expiry and fulfilled-order alerts, and does not read those flags. Graph warnings and report-success feedback are separate existing behaviors. |

For P01, counts are per batch and record expected quantity, counted quantity,
variance, user, date and notes. A nonzero variance creates a linked correction;
the correction must obey batch state, cost and initial-quantity limits. Counts
are not silently created by checkout or outflows. The stock-count POST does not
use the stock-adjustment submission UUID protocol; a future UI must not assume
that passing the outflow `request_id` contract makes stock-count retries keyed.
The ERD describes actual storage; including Stock Count there does not imply a
stocktaking screen exists. This review does not change the active schema.

## P10: forecast interfaces without UI callers

The dashboard uses `/api/forecasting/graph/` and GET/POST refresh/job status.
Combined/regular/feeding switching is local over the graph response. Graph
metadata already renders model/evidence information where supplied.

| Backend GET | Frontend state |
| --- | --- |
| `/api/forecasting/chart/` | `forecastingService.getChart` exists but is never referenced by source callers. |
| `/api/forecasting/forecast/` | `forecastingService.getRegularStatus` exists but is never referenced. |
| `/api/forecasting/evidence/` | No service/control for the standalone evidence report. |
| `/api/forecasting/weekly/` | No service/control for the standalone weekly research report. |
| `/api/forecasting/feeding/` | No service/control for the standalone feeding study. |
| `/api/forecasting/patterns/` | No service/control for standalone pattern studies. |
| `/api/forecasting/models/` | No service/control for standalone model status. |

These [views](forecasting/views.py) expose diagnostics/alternative representations.
Their underlying serving, evidence, learning and forecasting code is also used
by report generation, saved graph outputs or background maintenance. Absence of
these seven frontend requests does **not** make those backend modules disposable.
No model-activation or source-coverage verification screen exists; those actions
are operator management commands with publication/reconciliation gates.

The graph's development-only `scope=demo2026` is not selected by the UI; its
service explicitly restricts scope to `real`, and production rejects demo scope.
The separate `/api/forecasting/demo-2026/` endpoint always returns 410, and
`evaluate_demo_forecasts` explicitly fails. Neither is a missing live sales feature.

## Server pagination and summaries — retain

Keep list pagination, query validation, ordering/tie-breaking, summaries and
single-record reads in [sales/history.py](sales/history.py),
[transaction views](sales/views/transaction_views.py),
[encoding views](sales/views/encoding_views.py),
[order views](sales/views/order_views.py) and
[customer history](sales/views/customer_views.py).

| UI path | Current integration / remaining limitation |
| --- | --- |
| Admin Sales History | Uses server pages and transaction summary by default. Search/sort switches `useHistoryPage` to full-history downloads. |
| Sales Encoding, both roles | Uses server pages by default and individual detail reads. Search/sort switches to `encodingService.list` and full-history downloads. `getSummary` is unused; there are currently no encoding-summary cards requiring it. |
| Staff Sales History | `salesService.getMine` loads all matching pages, then filters client-side. Its personal totals do use the transaction summary endpoint. |
| Admin/Staff Orders | Both screens use `ordersService.getAll`. `getPage` and `getById` have no source caller. |
| Staff dashboard fulfilled-order count | Downloads all orders and filters by staff; does not use the order summary. |
| Admin dashboard | Uses transaction summaries and order summary. Do not remove them because other screens still load full history. |
| Staff recent orders | Reads a limited first server page through `getRecent`. |
| Customer History | Already uses server pages of ten with `include_voided`; this is not a full-history download. |

Backend history APIs support more filters than the screens expose: customer,
handler, unknown-payment states, inclusive date ranges, search and ordering.
Frontend `historyParams` can encode them, but current controls use only subsets.
Global history screens do not offer an include-voided toggle; Customer History
does. Search/sort currently falling back to local work is part of the retained
pagination request, not evidence that server search/sort code is unused forever.
Full filtered totals must continue to come from summaries, not one page.

The exact contract and deferred frontend work remain in
[FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md). No pagination code was removed or
rewritten by this review.

## Routes unused directly but covered through another interface

These 24 method bindings are not 24 missing features:

| Uncalled interface | Existing frontend equivalent |
| --- | --- |
| GET individual users, products, categories, customers and stock adjustments | Screens populate details from list responses; current user uses `/accounts/user/`. |
| PUT product/category/batch/customer detail | Existing update controls use PATCH, subject to the field limits above. |
| POST product `deactivate` | Delete already soft-deactivates protected products. A separate preserve-only deactivation action is not exposed. Product **reactivation** remains a distinct gap, P04. |
| GET `/inventory/fefo-config/` and GET/PUT/PATCH its detail route | Global thresholds are edited through `/settings/inventory/`, backed by the same model/serializer. |
| GET `/settings/system/` | Admin Settings reads the system object from `/settings/` overview. |
| PUT `/settings/system/`, `/settings/notifications/`, `/settings/inventory/` | Existing forms use PATCH. |
| GET/PUT/PATCH `/settings/system/{id}/` and `/settings/notifications/{id}/` | UI uses the canonical singleton routes. These detail aliases accept only ID 1. |

DELETE `/inventory/product-batches/{id}/` is also not used. The backend permits
deleting only an unused zero-quantity batch without protected links, and rejects
deleting received stock. UI disposal correctly records a stock adjustment;
do not replace disposal with batch deletion.

GET `/inventory/` and `/sales/` are browsable API-root metadata, not missing
business screens. POST `/api/forecasting/refresh/{job_id}/` always returns 405;
the UI correctly starts jobs at the collection URL and GETs the status URL.

Retired ingredient/supplier routes return empty collection reads or 410. The
frontend correctly stopped calling them. Old discount columns/neutral replay
compatibility do not mean discount pricing needs restoring: new non-neutral
discount input is rejected by [pricing policy](sales/services/item_pricing.py).

## Frontend service members with no source references

| Member | Interpretation |
| --- | --- |
| `ordersService.getPage` | Retain for pending server pagination integration. |
| `ordersService.getById` | Retain for selected records outside a server page. |
| `encodingService.getSummary` | Retain as the full filtered summary contract. |
| `forecastingService.getChart` | Unused alternative forecast representation. |
| `forecastingService.getRegularStatus` | Unused standalone monthly forecast status wrapper. |
| `inventoryService.getLowStock` | Deprecated adapter; actual UI uses `getLowStockProducts`. The backend alert feed is used. |
| `inventoryService.getNearExpiry` | Deprecated adapter; actual UI uses `getExpiringProducts`/inventory overview. The backend expiry feed is used. |
| `authService.refresh` | Unused alias only. The shared HTTP client directly invokes token refresh; `/accounts/refresh/` **is actively used**. |

Reference tracing includes service methods passed as callbacks and assigned to
local variables, e.g. batch editing. Merely searching for `.method()` would
misclassify those existing flows. Tests were not treated as application callers.

## Server-only code and data that remain necessary

| Area | Why there is no direct frontend CRUD page |
| --- | --- |
| `LoginSession`, token blacklist/session validation | Login, activity, refresh and logout use these indirectly. Idle-session management is integrated even without a session-administration page. |
| `SaleSubmission`, `StockAdjustmentSubmission` | Frontend request IDs drive durable deduplication/recovery. No dedicated table browser is required. |
| FEFO, `BatchSequence`, price validation, row/advisory locks | Server-side business rules execute on sales, stock and restock operations. |
| `StockReturn` | Order cancellation automatically records actual restoration outcomes; inventory reports use retained movements. |
| `NotificationState`, cache invalidation/signals, SSE | Used by inbox actions, committed writes and live refresh. They are not unimplemented UI entities. |
| Forecast model/issue/score/coverage/research tables | Background fitting/scoring, saved graphs and PDF reports. Detailed operator review is not all represented by dashboard controls. |
| Import/consolidation/cleanup/backup tools | The 21 custom management commands are not direct frontend APIs. Data import, reconciliation, gated activation, SMTP diagnostics and recovery deliberately remain operator actions. Some forecast commands are also launched indirectly by the worker/job flow. |
| Django migrations, provisioning/preflight/health scripts, packaging and QA tools | Deployment, permission checks, recovery and verification infrastructure. A frontend caller is not expected. |
| Retained legacy reporting-import database tables | Five old tables have no active Django models or frontend calls. They are retained data, not current frontend features; this audit does not authorize dropping them. |

## Result and next decisions

Record P01–P09 as the unexposed/partial capability inventory and P10 as diagnostic
coverage. They are review findings, not an instruction to implement every API
operation in the UI. Decide required business flows before changing the frozen
release. In particular, Stock Count cannot be described as a completed UI feature.

This documentation adds no runtime changes. Backend APIs, server pagination,
frontend source, database contents, Docker images and release tag are retained.
No test suite was rerun for these documentation changes; structural validation
checked route coverage, classification totals, service references and source links.
