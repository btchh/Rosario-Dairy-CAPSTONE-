# Local Docker preparation

The base stack runs the backend; the frontend can use its development server or
the optional Docker frontend built from its sibling checkout. It is a local deployment rehearsal.
For the separate production stack, database roles and HTTPS proxy configuration,
follow [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md). The actual hosting,
domain, SMTP account and backup/recovery setup still need configuring.

For an image archive your frontend teammate can load, and an optional frontend
service in the same stack, see [FRONTEND_DOCKER_HANDOFF.md](FRONTEND_DOCKER_HANDOFF.md).

The backend image contains application code, migrations and container startup
scripts. Tests, QA evidence, documentation, host deployment examples, local
environments and private data stay outside the image. Run automated test suites
from the source checkout; use the container checks below for runtime health.

To add the frontend to an already running backend, follow **Add the frontend
image to the same stack** in that guide. It builds with
`docker/compose.frontend.build.yaml` and starts only the frontend service;
the existing backend and database stay running. The local Docker frontend uses
port 5174 while the native Vite server occupies 5173.

Services are PostgreSQL 18, Redis, a one-shot migration/static-file job, Gunicorn,
an ASGI live-events service, the midnight forecast worker, and an Nginx gateway
for API requests and Django static files. The API stays at `http://localhost:8000`; the usual frontend origin
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
docker compose --env-file .env.docker logs migrate api events forecast-worker
docker compose --env-file .env.docker exec api python manage.py createsuperuser
```

The migration service must finish with exit code 0. It applies all migrations,
including `config.0001_initial`, then collects Django static files. API and worker
startup depend on its successful completion and healthy database/cache services.
The gateway waits for API and live-events health. These conditions use Docker
Compose's
[documented startup ordering](https://docs.docker.com/compose/how-tos/startup-order/).

The API health check verifies a database query, a short-lived cache round trip
and an unauthenticated user read returning 401. It does not consume login
throttles. The forecast worker runs maintenance immediately
on startup, then at midnight Asia/Manila. It uses the existing database lock to
coordinate with manual refresh jobs. Empty history produces unavailable estimates.

## Smoke test after the first build

```bash
curl -i http://localhost:8000/accounts/user/
curl -i http://localhost:8000/events/
curl -I http://localhost:8000/static/admin/css/base.css
docker compose --env-file .env.docker exec api python docker/healthcheck.py
docker compose --env-file .env.docker exec api python manage.py check
docker compose --env-file .env.docker exec api python manage.py showmigrations config
```

Expect HTTP 401 from the unauthenticated user and events endpoints, HTTP 200 for
the admin stylesheet, a successful health-check exit, and `[X] 0001_initial`. Log in with
the created administrator through the unchanged frontend, create a product and
batch, and verify a sale updates stock and reports. The fresh Docker database
has no existing accounts or sales; host-database credentials do not create users
inside it.

Opening `http://localhost:8000/` returns 404 because the backend has no root
page. Use the API checks above to verify it. The optional Docker frontend is
available on port 5174 on the preparation machine; the native Vite server uses
port 5173.

If `API_PORT` is changed, update the frontend API environment and matching
CSRF origins for that address. Keeping port 8000 preserves the existing URL.

## Live updates on OVHcloud or another VPS

The internal `events` service runs `uvicorn config.asgi:application` on port
8001, using the same image/environment/database/Redis as the API. Only the
gateway is published. `/events/` is routed to ASGI; all normal API paths continue
to use Gunicorn. `python docker/events_healthcheck.py` checks that the ASGI
listener returns 401 without a token. No database migration is added by this
feature. Source changes still require a new backend image.

The endpoint uses bearer-authenticated SSE. Successful business writes publish
after the outer database commit; notification failures cannot fail the sale.
The stream rechecks its session/shared version every 15 seconds, batches change
bursts, and rotates after five minutes or token expiry. It never sends records
or runs forecast jobs. The full event contract is in `FRONTEND_HANDOFF.md`, under
**Live updates across sessions**. The frontend implementation was verified in
the October 30 QA run; the remaining history pagination request is documented
in that handoff.

For a quick local native deployment, keep the API running normally and start
ASGI separately with the same database and `REDIS_URL`:

```bash
venv/bin/uvicorn config.asgi:application --host 127.0.0.1 --port 8001 --lifespan off --no-proxy-headers
```

Point the native gateway's `/events/` route at that listener. WSGI and Django's
standard `runserver` deliberately return 503 for authenticated stream requests;
they must not eagerly consume an endless asynchronous response. Redis is
required for streaming; ordinary API reads/writes still work without it in
development.

On an OVHcloud VPS, terminate HTTPS at the public reverse proxy and retain
unbuffered, uncached streaming for `/events/` at every proxy layer. The supplied
gateway and optional frontend proxy already set this; copy the behavior to the
outer TLS proxy. Keep idle timeouts above the 15-second heartbeat. Forward the
Authorization header. Configure trusted HTTPS forwarding explicitly using
`deployment.env.example`. The local examples set `X-Forwarded-Proto` to their
own `$scheme`; if a separate proxy terminates TLS, configure the trusted inner
hops to preserve the original HTTPS scheme (or use a fixed `https` value for a
production-only internal route). The public edge must overwrite client-supplied
forwarding headers. Enabling Django's proxy trust alone does not fix an inner
proxy replacing `https` with `http`. Do not expose the ASGI or database ports publicly.
With a separately hosted frontend, allow its exact HTTPS origin in CORS.

For the integration tests, use a disposable Redis instance, not the business
cache (one HTTP test clears its configured cache):

```bash
ROSARIO_TEST_REDIS_URL=redis://127.0.0.1:16379/0 venv/bin/python manage.py test config.test_live_events --noinput
```

The Redis tests are skipped unless that variable is supplied. They include two
viewers, missed publications, expiry/revocation, and an actual separate ASGI
process delivering a committed sale over HTTP with browser gzip/CORS headers.

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
docker compose --env-file .env.docker stop gateway api events forecast-worker
docker compose --env-file .env.docker build api
docker compose --env-file .env.docker up --no-build --force-recreate -d
docker compose --env-file .env.docker ps -a
```

The product-only cleanup adds `inventory.0012_product_only_inventory`. It removes
the unused supplier/ingredient tables and requires product batches on stock
movements. Back up the database before updating. If any retired records exist,
the migration stops before dropping tables; explicitly archive that data before
retrying. Do not delete migration history or recreate the database to bypass the
check. After the migration, invalidate old business cache payloads:

```bash
docker compose --env-file .env.docker exec api python manage.py shell -c "from config.data_cache import invalidate_data_cache; invalidate_data_cache()"
```

Rebuild and export a fresh image to share this cleanup; existing archives keep
their original code and schema. Product/customer/sales records are retained.

The named database volume persists through ordinary `down` and container
recreation. Keep `down --volumes` out of the normal update workflow because it
deletes these volumes. A named volume is not a recovery backup; take a separate
private database export before importing real data or changing the schema.

The sample PostgreSQL owner has local development administration privileges.
For production, use the standalone configuration described in
`PRODUCTION_DEPLOYMENT.md` with `deployment.env.example`. Then
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
