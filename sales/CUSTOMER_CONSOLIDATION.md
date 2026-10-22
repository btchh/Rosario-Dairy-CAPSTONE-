# Customer consolidation — October 23, 2026

The live database contained 364 customer accounts, including invoice/event date
annotations stored as separate customer names. The backed-up consolidation merged
34 redundant accounts across 21 groups, leaving 330 customers.

Examples:

| Consolidated account | Former variants |
| --- | --- |
| Ala Eh Festival | Ala Eh Batangas Dec.8; Ala Eh Dec 11/12/13 Sales; Ala Eh Festival Dec. 9/10 |
| Dairy Box Sales | Dairy Box; Dairy Box Aug 25; Dairy Box Aug.24 |
| Tikme | Tikme Aug 14-16; Tikme-Day 2/3 |
| Kadiwa-DBM | Kadiwa-DBM Dec 15-16 |
| BSU | BSU Day 3; BSU-MAIN remains separate |
| SM | SM Day 1–5; SMX remains separate |
| San Jose / Sta. Cruz | Day 1/2 variants within each account |
| Other formatting groups | Bombay-Lipa; Kadiwa-BIR; Kadiwa-San Pedro; Kadiwa-Sta. Cruz; Sta. Rosa; Sto. Tomas; MHO-Garcia |

Additional explicit alias groups include Members, Staff Sales, Camsur, Eat Plenty
Food Corp, Lito Ayson/Sir Lito Ayson and the existing Tri Star corporate alias
whose trailing period previously prevented matching.

## Preserved records and verification

The canonical existing account is retained where available; otherwise the oldest
ID is kept and renamed. Transaction and order customer foreign keys move to that
account. All receipts, line items, invoice numbers, original source labels, dates,
amounts, payment states, discounts, classifications and inventory remain intact.
Null-customer sales are not assigned to a named customer.

The command locks writes while saving a verified full logical database backup
and recording each old transaction/order customer link. It fingerprints every
non-customer table, excluding only the deliberately changed `customer_id` columns
on transactions and orders. Any unexpected change rolls back the merge. Contact
conflicts block a group; blank canonical contact details are filled only from a
non-conflicting member. An active member keeps the canonical account active.

Private backup and before/after audit:
`backups/customer-consolidation-20261023-001246-027390/`.
The completed manifest confirms identical protected fingerprints before/after.
On-demand forecast refresh job 3 completed successfully afterward because source
identity fingerprints include customer IDs. All nine graph combinations remain
available. No invoice revenue was changed for training. The 2025 customer report
now has one Ala Eh Festival account containing all six actual receipts.

## Prevent recurrence

`sales/customer_names.py` contains explicit aliases and bounded event/date rules.
The importer and customer API use the same canonical name function. A future
Ala Eh December date label resolves to Ala Eh Festival instead of creating a new
account. New API requests matching an existing name without conflicting contact
evidence return a validation error directing the user to the existing account.
Different people with the same name and conflicting contact details can remain
separate. This is application validation, not a globally unique-name constraint.

No schema migration or frontend implementation is required. Existing customer
lists, account histories and reports use the consolidated customer IDs; reload
those views after applying a consolidation. Original invoice labels can still
appear in source/history fields and should remain there as provenance.

```bash
# Read-only plan; no database changes.
venv/bin/python manage.py consolidate_customers

# Backup, lock, apply and verify atomically.
venv/bin/python manage.py consolidate_customers --apply
```

The command shares the forecast-maintenance lock and refuses to run during an
active fit. Repeating it after this consolidation finds no remaining clear
merge groups. It does not fuzzy-match arbitrary people or organizations.

## Owner-confirmed personal aliases — October 23, 2026

The owner subsequently confirmed these account identities and preferred names:

| Preferred account | Confirmed aliases |
| --- | --- |
| Jamie Ortiz | Jaime Ortiz; Jamie Ortiz |
| Jonny Decena | Jhonny Decena; Jonny Decena |
| Cassie Alagar | Cassie; Cassie A.; Cassie Alagar |
| Sir Sonny | Sonny; Sir Sonny; Sonny Bombay |

These explicit mappings are now part of the shared import/API normalization
policy. The second backed-up consolidation removes six further redundant
accounts, reducing the total from 330 to **324**. Existing accounts bearing the
preferred names retain their IDs. Original receipt labels and financial data
remain unchanged. The second backup/audit is
`backups/customer-consolidation-20261023-002001-008806/`.

## Identity questions still open

Other shortened personal names and business/branch abbreviations still require
source evidence. Invoice-like account labels such as SI03060 and SI03061 need
original-sheet identity review rather than assignment by guesswork.

Different Kadiwa locations, base institutions versus Kadiwa accounts, WTC branches,
BSU-MAIN, and feeding programs remain distinct. Unspecified feeding week labels
are not assigned to or merged with a known feeding program.
