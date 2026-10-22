# Refresh the historical dairy dataset

The original three workbooks are read without alteration. The command copies
them and the product policy into a new private audit directory before parsing.
Preview is the default; `--apply` performs a full database backup under PostgreSQL
table locks and updates source-referenced history atomically. Run while business
writes can briefly wait for the refresh to finish. Never commit backup files.

```bash
venv/bin/python manage.py migrate
venv/bin/python manage.py refresh_annual_sales /path/to/workbooks --output backups/dairy-preview
venv/bin/python manage.py refresh_annual_sales /path/to/workbooks --output backups/dairy-applied --apply --admin-username ADMIN
venv/bin/python manage.py refresh_forecast_patterns --output backups/dairy-patterns
```

Use a fresh output directory on every run. The files must be named
`SALES REPORT JAN-DEC 2023.xlsx`, `SALES REPORT JAN-DEC 2024.xlsx`, and
`SALES REPORT JAN-DEC 2025.xlsx`, each with twelve monthly sheets.

`sales/importers/dairy_policy.json` records every reviewed normalized product
label, including the user's explicit exclusion of pastillas/PDL, Pastiroll, and
Polvoron as sweets. Unknown labels default to review/exclusion. Edit this policy
after confirming a product's identity, advance its version, preview again, and
reapply from the original files. Labels are normalized using the existing
consolidated catalog rules, preserving distinct sizes/flavors. The refresh
requires an unambiguous existing product and customer mapping; it refuses to
overwrite manual encodings or receipts linked to current stock.

Each source sheet must reconcile against its printed monthly total before any
filtering. Cancelled and known duplicate invoices remain excluded. Retained
source line amounts and proportional invoice adjustments determine dairy revenue.
An invoice-only total containing both excluded and dairy products is held out
until original prices/amounts are confirmed. No residual total is allocated by
guessing. The audit keeps source totals, dairy totals, exclusions/holds, original
source issues, invoice-check discrepancies, all retained receipts, and every
excluded or held line. Per-sheet dairy plus excluded revenue reconciles exactly
to eligible source revenue. Check source monthly totals separately: they also
contain cancelled/duplicate rows prior to eligibility filtering.

A completed invoice `SUM(Gstart:Gend)` followed by an unlabelled row with its own
`=Grow` total is separated rather than inheriting the previous customer/SI.
Other subtotal groups, including feeding batches, stay together for review.
Ambiguous dates retain their written/inherited date and are flagged, never
silently moved into the worksheet's month. Source rows without enough identity,
date, or price evidence carry `source_sales_segment=unclassified`, remain visible
in dairy ledger reports, and are excluded from regular/feeding training.
The remaining source invoice-check discrepancies are exported for review;
monthly reconciliation alone does not certify every invoice grouping or date.

The command preserves existing receipt IDs/SIs for retained source invoices and
leaves payment status, payment updates, tendered amounts, handling staff and void
state unchanged. It updates linked order lines consistently. Non-dairy-only
source invoices leave the active dairy dataset; their complete originals remain
in the backup and exclusion audit. Product/customer catalog records and live
inventory balances are retained. Reserved demo invoices and manual sales are
outside the refresh scope. Reapplying unchanged sources/policy leaves receipt
and line IDs unchanged. Fingerprints verify all existing unrelated sales,
accounts, settings, catalog, stock, and research records before commit.

## Audit and recovery

The output directory contains `audit.json`, reconciliation/review CSVs, copied
sources/policy, and a manifest with SHA256 hashes. Apply also includes
`database-before.json.gz`, a Django base-manager JSON dump verified as readable
before any sales writes. `manifest.completed` and `manifest.applied` are true
only after commit; failed/unfinished audits must not be interpreted as applied.
Restore the dump to a separate empty database at the recorded schema and inspect
it before planning any live recovery. This is a logical Django backup, not a
native PostgreSQL cluster/roles backup. The initial refresh also has a full
working-source archive in `backups/dataset-refresh-20261022-145724/`.

Historical pattern studies retain their old revisions as audit history. Refresh
the current study after a dataset change. Neither dataset filtering nor a lower
research benchmark error establishes production forecast accuracy.


## Refresh the 2026 demo sales after a source refresh

```bash
venv/bin/python manage.py populate_2026_sales --replace-demo
venv/bin/python manage.py populate_2026_sales --replace-demo --apply
```

The preview is read-only. Apply backs up the full database, then replaces only
receipts whose reserved reference matches `2026:` plus exactly six digits and
their linked demo orders/items. Actual and manually encoded 2026 sales remain.
Existing demo entries with manual encodings, live stock links, payment updates,
voids or cancelled orders require review; the command refuses to replace them.
Protected-row fingerprints verify that all other existing records are unchanged.

Generation uses active customers' classified imported 2025 dairy baskets and
classified 2023–2025 customer trends. Unclassified source entries, held invoices,
non-dairy products and sweets are excluded; stale baskets with disallowed products
cause a source-refresh error. Month-end/weekday billing alignment, capped/damped
customer trends and deterministic quantity variation provide demonstration data.
The default seed is 2026; all twelve months are generated, including future demo
dates. These are simulated transactions, not predicted or actual future sales.

Demo notes explicitly identify the simulation and original source reference.
The private backup directory contains the original database, policy copy, source
mapping and manifest with hashes. Source SI numbers are never copied onto dummy
invoices. Receipt and item/order net amounts reconcile, and historical reference
batches are used without consuming current stock. Forecasting continues to
exclude all reserved demo references regardless of their regular/feeding label;
refreshing dummy sales must not alter the real-sales source revision.

## Data-only SQL backup of 2023–2025

```bash
venv/bin/python manage.py backup_sales_history --start-year 2023 --end-year 2025 --output backups/actual-sales-sql-2023-2025-20261023
```

This read-only, repeatable-read export writes PostgreSQL INSERT statements for
receipts in the requested completed **Asia/Manila business years**, their lines,
linked orders/order lines, and the customer/catalogue/account rows needed by
foreign keys and product/category snapshot references. It preserves voided,
unclassified and null-customer/null-SI historical entries. Reserved dummy sales
and out-of-range receipts are excluded. Referenced metadata is its current
corrected version, not a reconstructed historical catalogue or permissions backup.

The output contains `.sql`, `.sql.gz` and `manifest.json` with row fingerprints,
file SHA256 hashes and the applied migration list. It is a **data-only dataset
backup**, not a native pg_dump schema/roles/cluster backup. Restore only into an
empty application database at the recorded schema; it contains no DROP/TRUNCATE
or overwrite instructions. Identity sequences are advanced after explicit IDs.
Keep the files private: referenced account records include credential hashes.

Before marking completion, the exporter creates a uniquely named temporary
PostgreSQL database, creates the current model schema with DDL only (no application
data migrations), executes the SQL and compares every exported row fingerprint.
It drops that temporary database afterward. Live application rows are not changed.
Database creation privileges are therefore needed for the verification step.

The October 23 export restored successfully: **4,827 receipts, 24,251 receipt
lines, 4,822 orders and 24,239 order lines**, plus referenced dependencies. The
scope deliberately excludes the small 2022 fragment and all 2026 transactions.
Location: `backups/actual-sales-sql-2023-2025-20261023/`.
