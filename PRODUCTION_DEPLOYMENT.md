# Production Docker deployment

Use `compose.production.yaml` as a **standalone** deployment. `compose.yaml`
remains the local development stack; do not merge it into production. This
configuration targets a single OVHcloud VPS with host Nginx terminating HTTPS,
then the loopback frontend proxy, internal API gateway, Gunicorn and ASGI.

## Prepare the release

Keep the backend and frontend checkouts next to each other. Create a private
configuration file:

```bash
cp deployment.env.example .env.production
chmod 600 .env.production
```

Replace the placeholders with the actual domain, HTTPS browser origins, a
generated Django secret of at least 50 characters, SMTP sender and credentials,
and three distinct database passwords of at least 20 characters. Set
`DJANGO_HEALTHCHECK_HOST` to an explicit host in `DJANGO_ALLOWED_HOSTS`.
Generate independent secrets with `python3 -c "import secrets;
print(secrets.token_urlsafe(64))"`. Keep this file private and out of Git.

The bootstrap PostgreSQL administrator, migration/schema owner and Django
runtime role must have different names and passwords. Only the provisioning
service receives the administrator credentials; only the migration service
runs Django with schema-owner credentials. API, events, worker and preflight
use the restricted runtime account.

For Gmail SMTP, use `smtp.gmail.com`, port 587, TLS, the full Gmail address
and an eligible account's Google app password. `DEFAULT_FROM_EMAIL` should
identify that sender. SMTP wiring is provided, but delivery must be tested
with the real account on the VPS.

```bash
docker compose --env-file .env.production -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.production.yaml build api
docker compose --env-file .env.production -f compose.production.yaml \
  -f docker/compose.frontend.build.yaml --profile frontend build frontend
docker compose --env-file .env.production -f compose.production.yaml \
  --profile frontend up -d --no-build
docker compose --env-file .env.production -f compose.production.yaml \
  --profile frontend ps -a
docker compose --env-file .env.production -f compose.production.yaml \
  logs provision migrate preflight api events forecast-worker
```

The optional frontend build uses `FRONTEND_SOURCE`, defaulting to the sibling
`../SarimaHoesButFrontend` checkout. It compiles `/backend` as the API prefix.
Use release-specific backend/frontend image tags in the private environment
file and retain the previous images for rollback.

## Database initialization and existing data

This production project creates its **own empty database volume**. It does not
import the current local database. Provisioning grants runtime table CRUD and
sequence usage, including future objects created by the migration role.
Runtime schema/table creation, alteration and truncation are denied.

Provisioning is repeatable for a database already owned by the configured
migration role. It refuses elevated application roles, inherited roles, and
existing public objects owned by another role. It does not silently transfer
ownership of an existing installation. Moving current business data requires a
separate verified backup/restore and ownership migration into the production
database before accepting traffic. Do not attach the local data volume or
change its administrator account to bypass these checks.

Startup runs provisioning, migrations/static collection, then preflight before
starting API/events/worker. Preflight checks the actual runtime database role,
Django deployment checks and pending migrations. Warnings and errors block
startup except `security.W005` and `security.W021`: HSTS subdomain enforcement
and preload remain explicit operator decisions. These advisories are printed;
positive HSTS, HTTPS redirects and secure cookies remain required. Do not
enable preload/subdomain enforcement until the corresponding domain policy
and certificates are ready.

## Public HTTPS edge

Adapt `docker/edge.nginx.example.conf` into the host Nginx configuration after
obtaining the domain certificate. Replace the domain, certificate paths and
upstream port to match `FRONTEND_PORT`. Configure certificate renewal. Only the
host edge accepts public traffic; the Compose frontend and gateway bind to
loopback. Database, Redis, Gunicorn and ASGI ports have no host publication.

The edge overwrites `X-Forwarded-For` with one client address and
`X-Forwarded-Proto` with `https`. Inner proxies preserve that canonical address
and send the trusted HTTPS scheme. Django therefore uses `NUM_PROXIES=1` for
this supplied chain. Changing to a CDN/load balancer or another proxy layout
requires reviewing trusted client-IP handling before deployment. Do not expose
the internal proxies directly to the internet.

Health probes call their own process on loopback with the allowed health host
and configured HTTPS forwarding header. They expect an unauthenticated 401,
not a redirect. Every proxy keeps SSE unbuffered and uncached. The frontend
application in frontend `63ecff33` implements the live-event client described in
`FRONTEND_HANDOFF.md`; deploy that image alongside the matching backend.

After startup, create the initial administrator using the API service's Django
management command. Verify authenticated API/history reads, a sale and stock
update, reconnecting event streams, password-recovery email delivery, and
reboot recovery through the actual public HTTPS URL.

## Operations required before accepting real traffic

Configure the VPS firewall and certificate renewal; schedule full database
backups outside the VPS with retention and a tested restore; configure service,
backup and forecast-job alerts; and check workload capacity on the chosen VPS.
Container logs rotate at 10 MB with three files, but rotation is not monitoring.
The historical sales SQL export is not a full database recovery backup.

Before each release, take a verified backup, retain the previous release image,
build the new image and rerun the production startup/preflight sequence. A
rollback involving schema changes needs a reviewed database recovery plan.
Ordinary container recreation preserves named volumes. Never use
`down --volumes` on the real deployment as an update command.

See `BACKEND_VALIDATION.md` for local verification and
`BACKEND_DEPLOYMENT_READINESS.md` for remaining hosting checks. Local smoke
results do not qualify the eventual OVHcloud deployment.
