# Local Docker preparation

This stack runs the backend while the existing frontend stays on its current
development server. It is a local deployment rehearsal. Production hosting,
TLS, SMTP and separate migration/runtime database roles still need configuration.

For an image archive your frontend teammate can load, and an optional frontend
service in the same stack, see [FRONTEND_DOCKER_HANDOFF.md](FRONTEND_DOCKER_HANDOFF.md).

Services are PostgreSQL 18, Redis, a one-shot migration/static-file job, Gunicorn,
the midnight forecast worker, and an Nginx gateway for API requests and Django
static files. The API stays at `http://localhost:8000`; the usual frontend origin
remains `http://localhost:5173`. The Docker database starts empty in its own named
volume. Existing host data is not imported or mounted automatically.

## First start

Install Docker Engine/Desktop with Docker Compose and Buildx first. The
preparation machine uses the standard Arch packages: Docker 29.9.2, Compose 5.6.1
and Buildx 0.38.1. Docker is enabled at boot and running as a system service.
The current user belongs to the `docker` group; log out and back in to apply
that membership to existing desktop sessions, or use `newgrp docker` in a
terminal. Check `docker info` before starting the stack.

```bash
cp docker.env.example .env.docker
chmod 600 .env.docker
```

Replace both secret placeholders in `.env.docker`. A generated, private
`.env.docker` is already available on the preparation machine; retain it instead
of overwriting it. Generate secrets with `python -c "import secrets;
print(secrets.token_urlsafe(64))"` when preparing another machine. This file is
Git-ignored and excluded from the image build context.

Stop the native Django server if it is using port 8000. Then run:

```bash
docker compose --env-file .env.docker config --quiet
docker compose --env-file .env.docker build api
docker compose --env-file .env.docker up -d --no-build
docker compose --env-file .env.docker ps -a
docker compose --env-file .env.docker logs migrate api forecast-worker
docker compose --env-file .env.docker exec api python manage.py createsuperuser
```

The migration service must finish with exit code 0. It applies all migrations,
including `config.0001_initial`, then collects Django static files. API and worker
startup depend on its successful completion and healthy database/cache services.
The gateway waits for API health. These conditions use Docker Compose's
[documented startup ordering](https://docs.docker.com/compose/how-tos/startup-order/).

The API health check verifies a database query, a short-lived cache round trip
and an unauthenticated user read returning 401. It does not consume login
throttles. The forecast worker runs maintenance immediately
on startup, then at midnight Asia/Manila. It uses the existing database lock to
coordinate with manual refresh jobs. Empty history produces unavailable estimates.

## Smoke test after the first build

```bash
curl -i http://localhost:8000/accounts/user/
curl -I http://localhost:8000/static/admin/css/base.css
docker compose --env-file .env.docker exec api python docker/healthcheck.py
docker compose --env-file .env.docker exec api python manage.py check
docker compose --env-file .env.docker exec api python manage.py showmigrations config
```

Expect HTTP 401 from the unauthenticated user endpoint, HTTP 200 for the admin
stylesheet, a successful health-check exit, and `[X] 0001_initial`. Log in with
the created administrator through the unchanged frontend, create a product and
batch, and verify a sale updates stock and reports. The fresh Docker database
has no existing accounts or sales; host-database credentials do not create users
inside it.

Opening `http://localhost:8000/` returns 404 because the backend has no root
page. Use the API checks above to verify it. The frontend runs separately on
port 5173 until the frontend developer supplies and tests their image.

If `API_PORT` is changed, update the frontend API environment and matching
CSRF origins for that address. Keeping port 8000 preserves the existing URL.

## Storage and updates

PostgreSQL data, private backend backups/job logs, and collected static files use
named volumes. PostgreSQL 18 uses `/var/lib/postgresql/18/docker` internally and
the volume mounts at `/var/lib/postgresql`, following the
[official image's PostgreSQL 18 layout](https://hub.docker.com/_/postgres).
Database and Redis ports are not published. Only the gateway binds to host
loopback. Redis cache data is disposable and is rebuilt after a restart.

The Python image runs as UID/GID 10001, with a read-only application filesystem
and writable backup/static volumes plus temporary storage. Local secrets, the
virtual environment, host backups and private research artifacts are excluded
from the image. Source edits require rebuilding; this setup does not bind-mount
the repository or auto-reload Gunicorn.

After code changes, stop the application services, rebuild, and rerun startup so
the migration job runs before the new backend:

```bash
docker compose --env-file .env.docker stop gateway api forecast-worker
docker compose --env-file .env.docker build api
docker compose --env-file .env.docker up --no-build --force-recreate -d
docker compose --env-file .env.docker ps -a
```

The named database volume persists through ordinary `down` and container
recreation. Keep `down --volumes` out of the normal update workflow because it
deletes these volumes. A named volume is not a recovery backup; take a separate
private database export before importing real data or changing the schema.

The sample PostgreSQL owner has local development administration privileges.
Before production, provision a dedicated runtime role, migration credentials,
HTTPS proxy/origins, Redis and real SMTP using `deployment.env.example`. Then
verify `manage.py check --deploy --database default` with the runtime credentials.
Review the official [Python image](https://hub.docker.com/_/python) and pin tested
image digests for release reproducibility after the first successful build.

New installations start with an empty database. On 2026-10-29 the preparation
machine copied its existing local database into Docker after saving private
source/target backups. Exact row counts and canonical content hashes matched
for all 56 tables, including retained historical tables. The copied dataset
contains 4,835 transactions, 324 customers, 170 products and 198 product batches after cleanup.
The host database was preserved during transfer. Subsequently, the user's sole
2026 test receipt (#41886, PHP 4,000) was backed up and removed from both host
and Docker databases, and its 100 BB200 units were restored. Unrelated records
were verified unchanged; both databases have no remaining 2026 transactions.

A separate private backend-with-data package can seed the frontend developer's
own Docker database. Its START_HERE.md and restore-database.sh describe the
backup/restore process; DEV_LOGIN.txt supplies a new development-only admin
login. Existing account passwords and session/reset tokens are removed only
from that shareable snapshot. The normal source/image archive still excludes
database contents and private environment files.

## Verification on 2026-10-29

The Linux amd64 image built successfully on Arch Linux. All migrations and
static collection completed, the database/cache/API health checks passed, and
the forecast worker completed its first refresh against the empty database.
HTTP checks returned 401 for an unauthenticated user, 200 for an admin stylesheet,
and 400 for malformed login JSON. CORS allowed the existing localhost:5173 origin.
The 20 health/deployment/scheduler tests also passed inside the API container.

The image runs as UID 10001 without local environment files, virtual environments
or private backups. The application filesystem rejects writes while backup,
static and temporary paths remain writable. The optional frontend proxy was
tested with a stock Nginx image, including `/backend/` forwarding and SPA fallback;
the teammate's actual frontend image still needs to be built and tested.

Readiness recheck on 2026-10-29 passed 168 customer/sales, Docker health,
deployment-settings and scheduler regression tests inside the backend image.
The test process used an isolated in-memory cache and a fast test-only password
hasher; application runtime settings were unchanged. Both Compose configurations
validated, the live API/database/Redis health checks passed, migrations were
current, and the gateway returned the expected user 401 and static-file 200.

## Keep 2026 empty in the development dataset

The three remaining 2026 product batches and one disposal adjustment were backed
up and removed from both local databases. The active data now has 4,835 receipts,
324 customers, 170 products and 198 historical reference batches, with zero
recorded 2026 sales/orders/stock activity. Historical imports were preserved.

Forecasts for 2026 remain visible in charts and reports. The cleanup removes
recorded business activity only; forecast projections, saved model artifacts and
issued forecasts are retained. A forecast does not imply recorded sales.
