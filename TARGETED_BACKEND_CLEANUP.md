# Targeted backend cleanup — 2026-11-01

Source cleanup after `74ee7d9e`, reviewed against frontend `5e390189`.
**Implemented and tested; not yet deployed into Docker.** No frontend source,
running database or frozen `release-2026.11.01` image/package/tag was changed.

## Removed

| Obsolete code/schema | Result |
| --- | --- |
| Retired ingredient/supplier compatibility regex and view | `ingredients`, `ingredient-batches`, `suppliers`, `low-stock/ingredients`, `expiring/ingredients` paths now return 404 rather than empty reads/410. No current frontend caller. |
| Disabled `/api/forecasting/demo-2026/` endpoint, demo compatibility module and `evaluate_demo_forecasts` command | Endpoint is 404; command is unavailable. Neither generated live forecasts before cleanup. Archived experiment data is retained. |
| Obsolete Stock Count | Model, serializer, API and reconciliation service removed. `inventory.0014_remove_obsolete_stock_count` locks and drops its table only when empty; otherwise it aborts without discarding history. |
| `forecast_warnings`, `report_ready_notifications` | Removed from Notification Settings model/API; migration `systemsetting.0003_remove_inactive_notification_flags` drops only these two no-effect columns. |

All 113 remaining declared method bindings are preserved: 75 route patterns,
73 with frontend call sites and four with unused service wrappers. All frontend
request paths from the original source audit still resolve. Twenty custom
management commands remain. Alternative routes and live forecast diagnostics
remain available because absence of a UI caller alone is not evidence of dead
backend functionality. The working graph's development-only demo scope and its
production restriction are unchanged.

## Database review and Stock Count

**Stock Count is obsolete by user decision and removed from the working source.**
The model/API/serializer, per-batch count reconciliation service, import/QA
references and feature-specific tests are removed. Its table contains zero
records in the running database; the new migration checks again under a lock
before dropping it. If another installation has counts, migration stops so that
history can be explicitly backed up and archived. Stock quantities, adjustments,
corrections, FEFO and durable stock-out retry protection remain implemented.
The running database awaits the coordinated rollout described below.

The running local database still has **55 public tables**: 49 original
active-model tables,
`django_migrations` and five legacy reporting-import tables. After both cleanup
migrations, the source schema has **48 active-model tables**, and the restored
database has **54 public tables** (48 active + migration history + five legacy).
No unvalidated
constraints or invalid indexes were found. These checks are catalog/integrity
checks, not an exhaustive physical corruption or performance audit.

| Retained legacy table | Rows at review |
| --- | ---: |
| `reporting_historicalsale` | 1,590 |
| `reporting_historicalsalesday` | 333 |
| `reporting_importedrow` | 19,005 |
| `reporting_importedsheet` | 201 |
| `reporting_workbookimport` | 13 |

These are populated historical imports without current Django models, not empty
throwaway tables. No foreign keys from non-reporting tables into them were found;
that does not establish that their historical data is disposable. They remain
intact. Only the empty obsolete stock-count table is scheduled for removal; no
count history or retained business records are discarded.

## Verification

- **985 unique backend tests passed; one native-Windows ACL test skipped.**
  The initial seven-app run discovered 986 tests in 561.934 seconds and
  found one new migration-test fixture error (historical user schema omitted
  `token_version`), with six opt-in Redis skips plus Windows. After fixing only
  that fixture, all six non-forecast apps reran with disposable Redis: **752
  tests, 751 passed, one Windows skip**, in 91.579 seconds. All 234
  forecasting tests had passed in the initial run; their code was unchanged.
  The six Redis integration tests also passed independently.
  This is backend regression coverage, not a new browser acceptance run.
- Migration regressions prove that an empty count table is removed without
  changing stock, and that populated count history blocks removal while keeping
  the count and its linked adjustment. Historical migration tests restore the
  current schema before subsequent tests run.
- A fresh private full backup restored into isolated PostgreSQL. Both migrations
  passed forward/reverse/forward checks: 55 public tables become 54, removing
  only `inventory_stockcount` (zero rows). Counts and retained-value fingerprints
  of all **53 retained non-migration tables** match. Only the two inactive
  notification columns are removed from retained tables; migration history gains
  two entries. Stock quantities, adjustments, submission records, existing alert
  preferences and all five legacy reporting tables are preserved.
- Django checks, migration drift/pending-migration checks passed. Resolver
  comparison confirms only nine retired bindings removed, three belonging to
  Stock Count. All current frontend paths, pagination, summaries and retained
  detail bindings remain: **75 route patterns and 113 method bindings**.
- ERD PDF/dictionary now have **48 active tables and 48 foreign keys**. All
  remaining model metadata is unchanged from the previous source schema;
  complete-diagram fields and module PK/FK coverage match. The PDF is eight A3
  pages; Stock Count is absent. Earlier source/PDF snapshots are archived in
  `../docs 2/erd/archive/2026-11-01-before-stock-count-retirement/`.

Current sanitized evidence:
[validation/stock-count-retirement-20261101.json](validation/stock-count-retirement-20261101.json).
The [initial cleanup evidence](validation/targeted-cleanup-20261101.json) records
its earlier 274 tests before the user classified Stock Count as obsolete. The
[original parity inventory](validation/frontend-parity-20261101.json) is also a
historical snapshot, not the current list of routes/models.
Private backup: `../private-database-backups/stock-count-retirement-20261101/before-cleanup.dump`
(outside Git/public packages).

## Rollout and frontend handoff

Deploy this source with a new backend image and apply both migrations during a
coordinated backend rollout, with API/events/worker writers stopped and a fresh
cutover backup. Old images still select the removed columns, so do not migrate
the running database while old backend services are serving. The current Docker
stack remains on its previous schema/image. The finalized release archive/tag
is unchanged; it does not yet include this cleanup.

Reversing the migrations recreates the empty Stock Count table and restores
the inactive columns with their former model defaults, not their discarded values. Working notification preferences and all
business records are retained in either direction.

The [frontend handoff](FRONTEND_HANDOFF.md) asks only for removing the two unused
notification type properties, in addition to the existing deferred pagination
request. Existing Settings forms already use only the three active flags. Stock
Count is obsolete and retired; no new stocktaking UI is requested here.
