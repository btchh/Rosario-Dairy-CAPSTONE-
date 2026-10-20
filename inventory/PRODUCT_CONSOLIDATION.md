# Product consolidation

Preview records differing only in whitespace, capitalization or parentheses:

```bash
python manage.py consolidate_products
```

Apply with a full database backup and audit manifest:

```bash
python manage.py consolidate_products --apply
```

The command requires PostgreSQL, locks tables against concurrent writes and
merges all groups in one database transaction. Run during a quiet period;
other writes wait while the backup and merge run. Repeating the command after
a successful merge makes no further changes.

The oldest product ID and its category survive. The shortest existing spelling
becomes the displayed name. A product stays active if any merged record was
active. Different units or variants remain separate, as do different sizes,
flavors, pouch labels, and ambiguous abbreviations. Punctuation such as decimal
points, hyphens and slashes remains significant.

Batches and order lines move to the surviving product. Batch IDs and their
dates, quantities, prices and adjustment/count history stay intact. Sale item
product and category snapshots use the surviving identity for reporting;
original source labels remain available, with the previous snapshot label
copied there when the source label was empty. Sale prices, quantities,
discounts, payments, invoice numbers and transaction totals stay intact.

Backups and manifests are under the git-ignored `backups/product-consolidation-*`
directory. The manifest records every source and surviving ID/name/category.
The command fingerprints every other database table and column except the
intended product references and labels, rolling back on unexpected changes.

Product create/update API validation rejects equivalent names with the same
unit and variant, including inactive products. Existing differently sized or
flavored products can still be created. Direct ORM writes/import scripts bypass
API validation; run the preview after such imports if needed.
