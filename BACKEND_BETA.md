Backend working beta — October 15, 2026

Rollback references:

- Branch: `beta/backend-2026-10-15`
- Annotated tag: `backend-beta-2026-10-15`

This checkpoint preserves the current backend, migrations, tests, and offline
SARIMA experiments before further customer/product forecasting changes.
The production evaluator remains `daily-sarima-v1`; experiment results do not
automatically replace production or certify future accuracy. The dashboard demo
and its endpoint have been removed. Earlier simulation storage remains isolated
from real-sales forecasting.

Real-data experiments available in this beta:

```bash
venv/bin/python manage.py compare_segmented_sarima --workers 4
venv/bin/python manage.py tune_sales_sarima --workers 4
venv/bin/python manage.py compare_customer_sarima --workers 4
```

See the corresponding documents in `forecasting/` for protocols and limitations.
Customer-based monthly regular-sales WAPE was 12.19% on the six July–December
2025 retrospective test months; combined monthly WAPE was 35.89%. This does not
establish sub-15% total-sales or prospective accuracy. Customer purchase gaps are
retained as recorded and do not label customer relationships as terminated.

To create a source restoration branch from this checkpoint:

```bash
git fetch origin --tags
git switch -c restore/backend-beta-2026-10-15 backend-beta-2026-10-15
```

Git preserves source code and migrations. Local `.env`, virtual environments,
database contents, private forecast exports, and uploaded files are not in Git.
A private compressed application-data fixture is kept locally at
`backups/backend-beta-2026-10-15/data.json.gz`; its manifest records the checkpoint
commit and checksum. This fixture includes application records and saved
experiments, but does not include PostgreSQL roles, server configuration, or
uploaded file contents. To restore application data, use a fresh database
migrated with this revision, then load the private fixture with `manage.py
loaddata`. Database restoration is separate from changing the Git revision.
