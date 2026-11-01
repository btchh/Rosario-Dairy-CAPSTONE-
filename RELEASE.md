# Finalized release — release-2026.11.01

Finalized on 2026-11-01 (Asia/Manila) for deployment preparation. This freezes
the tested application and records the accepted limits. Target-host setup and
public production acceptance follow [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

## Frozen application

| Component | Revision / image |
| --- | --- |
| Backend application source | `df6f732a` (subsequent changes are release records/packaging) |
| Backend release records | Git tag `release-2026.11.01` |
| Frontend source | `5e39018993a55461e48b30dedc4ff24132be8c38` |
| Backend image | `rosario-dairy-backend:release-2026.11.01` |
| Frontend image | `rosario-dairy-frontend:release-2026.11.01` |

Backend image ID:
`sha256:6e76d80935c1bf2174d7ab4f662c16d15814858c301f9187607f6009d7bff7a0`.
Frontend image ID:
`sha256:6d9207b5b6465bdbda12ae6666503c82ea543b781ab7fd3d6339f77917a4790d`.
The rebuilt backend has identical filesystem layers/configuration to the current
running backend. The latest frontend is running locally at `http://localhost:5174`.

Migration `inventory.0013_stock_adjustment_submission` is applied locally. Keep
all migrations in the package and apply the complete pending migration plan on
the target database using migration credentials.

## Verification and accepted limits

- Backend: 991 tests run, 990 passed, one Windows-only ACL skip.
- Latest frontend: all 662 tests, TypeScript and Docker production build passed.
- Broad audit: 12 actual HTTP workflow groups, 17 role screen loads, real
  login/logout and six browser PDF downloads passed against an isolated restore.
  That browser audit used `840f08ea`; the subsequent small `5e390189` Staff Orders
  update passed the latest frontend suite and asset/proxy checks.
- Current local services are healthy, Django checks pass and no migrations are
  pending. Previous full backups were restored and verified in isolation.

**Pagination is deferred by the user for this release.** Several screens still
fetch full histories, with the associated loading cost. Preserve the API contract
in [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md) for later work.

Forecast missing-data/freshness warnings and `production_approved=false` states
remain. Full browser write-form journeys, responsive layouts, PDF visual review
and the reported longer Sales History update delay are not signed off. Read
[DEPLOYMENT_FEATURE_AUDIT.md](DEPLOYMENT_FEATURE_AUDIT.md) for the exact evidence
and gaps; finalizing a version does not extend that evidence.

## Deployment package

The local package is `../docker-exports/rosario-release-2026.11.01.tar.gz`, with
an adjacent SHA-256 checksum. It contains committed backend/frontend source,
five Docker images (backend, frontend, PostgreSQL, Redis and Nginx), a release
manifest, pinned-image Compose override, deployment instructions and file
checksums. Image platform is **linux/amd64**.

Private environment files, SMTP credentials, database contents and backups are
excluded. Existing business data needs a separate private, verified transfer;
starting the production project alone creates an empty database.

Extract the archive, verify its `SHA256SUMS`, then follow its `START_HERE.md`.
It loads saved images and uses the standalone production Compose file with
the release override. Configure the actual domain, independent secrets and
email settings before startup. Do not expose the local development Compose
configuration unchanged. No public server was deployed during finalization.

Previous images and verified private backups remain available for rollback;
see [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md). Never delete the real data
volume during an update. Restore/migration ownership and external SMTP delivery
must be verified on the selected host before accepting public traffic.
