# Yearly SI production deployment — 3 November 2026

The backend is deployed at https://rosariodairy.duckdns.org as
`release-2026.11.03-yearly-si.1`, from commit
`279ab4508727f27881ecda079659b7e1489144ef`.
The frontend source, image and running container are unchanged.

POS, Orders and blank-SI Encoding share a yearly sequence. Encoding uses the
selected sales year and preserves a supplied paper SI. Paper references may
overlap automatic numbers; repeated paper references in the same year return
409. Request UUIDs continue to protect retries independently of invoice numbers.
Existing historical invoice numbers have not been rewritten.

## Verification

- Production provisioning, migration `sales.0018_yearly_invoice_numbers` and
  restricted-runtime-role preflight passed. There are no pending migrations.
- All 267 runtime source files match the committed, tested backend source.
- All 4,835 retained transactions and the retained fields in 51 tables were
  unchanged across migration. All 14 sales/inventory tables matched again
  after the authenticated public checks.
- All 34 authenticated public checks passed, covering admin/staff permissions,
  histories, summaries, reports/PDFs and rejection of a duplicate paper SI.
- Actual production-role number allocation returned `SI0001` then `SI0002`.
  Those allocation probes were rolled back; no sale or stock movement was
  committed. Both temporary verification sessions and their token records were
  removed by exact ID.
- The Compose wrapper points at the new release. Boot startup, daily backups
  and certificate renewal remain enabled. Existing database, Redis, gateway
  and frontend images remain pinned to their previous versions.

The earlier local checks ran 831 tests with one Windows-only skip. After the
final order/sale date alignment, all 327 sales/reporting tests passed with the
same skip. This includes 24 new numbering tests. See the
[local evidence](validation/yearly-si-20261103.json) and
[public deployment evidence](validation/yearly-si-vps-20261103.json).

## Recovery checkpoint

The previous backend is commit `9bf6c105` and image
`sha256:827af503adf15cbc5452ae7d190fbd9e5dda45db6544f6d7baa72efc2f633d95`.
Its release directory, image and protected Compose wrapper are retained.

A fresh quiesced VPS backup was downloaded privately, checksum-verified and
restored into an isolated database; all 51 retained-table fingerprints matched.
Applying and reversing the SI migration was also rehearsed on an isolated VPS
backup without changing the retained records. Production recovery artifacts are
protected under `/opt/rosario/private/yearly-si-20261103/`, with a separate
private copy on the operator machine. Database dumps and credentials are not
included in Git or the application release archive.

If rollback is necessary, use the protected operator helper:

```bash
sudo /opt/rosario/private/yearly-si-20261103/deploy-or-rollback.sh rollback
```

It stops backend writers, takes a new backup, reverses only the SI migration,
restores the previous Compose wrapper and backend, and reloads the proxies.
Reversing the migration removes the new counter/provenance metadata while
retaining transaction rows and their recorded SI strings. It does not restore
an older database over later business records. Reverting application files
alone is insufficient because the schema has changed.
