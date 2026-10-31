# Backend deployment readiness — 2026-10-30

**Decision: not ready for public production deployment as currently configured.**

## Current local release — 2026-10-31

Frontend `f6e4546a`, the session-enforcing backend image, and migration
`accounts.0005_loginsession` are now deployed together locally. The 15-minute
idle policy/one-minute warning passed authenticated admin/staff browser checks,
including continuation, activity/background-read distinction, expiry and
independent-session preservation. Frontend 614 tests/TypeScript and backend
736 tests (one platform skip) passed. Gmail authentication, service health and
migration checks passed. The full pre-migration backup restored in isolated
PostgreSQL, and all 19 existing business-table contents were unchanged.
Stock-out idempotency is now implemented in backend source, pending its Docker
rollout and additive `inventory.0013_stock_adjustment_submission` migration.
The frontend handoff covers durable stock-out request IDs and the remaining
server pagination work. Existing unkeyed requests do not gain replay protection.
Public OVHcloud/domain/TLS/operations verification below remains outstanding;
local deployment does not establish public production readiness.

## SMTP follow-up — 2026-10-31

The running local image was confirmed to use console email, with no SMTP
credentials. Earlier native email behavior is not inherited by Docker. The
local Compose definition now forwards optional `SMTP_*`/`DEFAULT_FROM_EMAIL`
settings, and `check_smtp` in the newer source explicitly rejects console mail
as a delivery check. Production Compose already required SMTP configuration.
After the user supplied the Gmail App Password privately, API/events/worker
were recreated with their existing image. Gmail TLS connection and SMTP
authentication passed both before activation and from the running API. API/
events health, Django/migration checks and frontend/gateway HTTP checks passed.
No email or reset challenge was sent by the agent during verification. The user
subsequently requested an OTP and confirmed successful delivery. API health
alone does not cover mail. Frontend `7295f413` was built/deployed locally and
passed 580 tests, TypeScript and authenticated browser smoke checks. Idle-session
frontend integration was still pending at that stage; the coordinated release
above subsequently activated it. Some full-list history paths remain. The OVHcloud operational
requirements below remain release requirements. Local SMTP authentication
does not establish public deployment readiness.

## Production configuration follow-up

The original findings below describe the local deployment and are retained as
the audit record. A separate `compose.production.yaml` now wires production
settings/SMTP, provisions separate administrator/migration/runtime database
roles, gates startup on deployment checks, and supplies internal HTTPS-aware
proxies and health probes. `PRODUCTION_DEPLOYMENT.md` documents its use and the
public TLS edge example. The original local stack was preserved during that
isolated production verification. A subsequent frontend pull to `63ecff33`
rebuilt and updated the local frontend/backend images, events service and
proxies. Their service/HTTP health checks passed; this is still a local stack.

Verification resumed after the interruption: the backend image built and 42
focused deployment, health and connection-security tests passed. A disposable
production project passed provisioning, migrations, preflight, API/events health
and the worker's initial maintenance run. Runtime database privileges, forbidden
DDL, normal CRUD and two-viewer live updates were checked against that project.
See `BACKEND_VALIDATION.md` for the final measurements and verification scope.

These changes address the repository configuration defects in findings 1–3.
They do not configure the real OVHcloud server, transfer the current data,
deliver a real SMTP email or schedule external backups. Those remain release
requirements. The local image update does not deploy the OVHcloud production
configuration. HSTS subdomain and
preload advisories remain visible and require domain-policy decisions.

## Original audit scope

The application has substantial functional verification, but the supplied
Compose stack is a local deployment rehearsal. Frontend finalization alone
does not resolve the production configuration and operations findings below.
This audit inspected backend `df7c3753`, the local working-tree packaging,
the running local stack, and the previously built live-events candidate image.
It did not change application settings, credentials, database privileges,
business records or running backend services.

## Confirmed blockers

### 1. Production settings are not wired into Compose

`compose.yaml:8` fixes `DJANGO_ENV` to `development`. Its environment mapping
does not forward `SMTP_*`, `DEFAULT_FROM_EMAIL`, `DJANGO_BEHIND_HTTPS_PROXY` or
the production security settings from `deployment.env.example`. Passing that
file to Compose for interpolation will not inject unreferenced values into
containers or replace the literal development setting.

Reproduction: resolving Compose with shell values `DJANGO_ENV=production`,
`SMTP_HOST=smtp.example.test` and `DJANGO_BEHIND_HTTPS_PROXY=true` still produced
`DJANGO_ENV=development` and omitted the SMTP/proxy variables from the API
container. The runtime confirms development mode, console email, no SMTP,
disabled HTTPS redirect, insecure session/CSRF cookies and zero HSTS.
`check --deploy` returned security.W004, W008, W012 and W016.

Required change: provide a production Compose override or separate deployment
definition which explicitly forwards the real production settings. Configure
the actual domain/origins, a unique production secret and SMTP credentials.
Validate delivered password-recovery email on the deployed host. Development
defaults should remain available for the existing local stack.

Reference: [Docker environment precedence](https://docs.docker.com/compose/how-tos/environment-variables/envvars-precedence/).

### 2. The web application uses an elevated database role

`compose.yaml:16` and `compose.yaml:37` reuse the same `DB_USER` for Django and
PostgreSQL initialization; the migration process also inherits those credentials.
The running role has all five elevated flags enabled: superuser, database
creation, role creation, replication and row-security bypass. Enabling the
existing production role check returns `config.E002`.

Required change: provision separate database administration/migration and
application runtime credentials, with appropriate schema/table/sequence grants.
The runtime role must pass `check --deploy --database default`. Changing the
user's name or an environment variable does not remove existing privileges.

Reference: [PostgreSQL image initialization](https://hub.docker.com/_/postgres)
creates `POSTGRES_USER` with superuser privileges on an empty data directory.

### 3. HTTPS forwarding and health checks need a production configuration

`docker/nginx.conf:18` and `docker/nginx.conf:30` replace forwarded protocol with
the inner proxy's `$scheme`. The optional frontend proxy does the same at
`docker/frontend.nginx.conf:15` and `docker/frontend.nginx.conf:26`.
If HTTPS terminates at an outer OVHcloud/VPS proxy and these inner hops use HTTP,
Django receives `http`; enabling strict HTTPS can then cause repeated redirects.
The current application also has no configured HTTPS proxy trust.

Both `docker/healthcheck.py:34` and `docker/events_healthcheck.py:9` make an HTTP
request with `Host: localhost` and require 401. With strict production hosts and
HTTPS these probes can instead receive 400 or 301, making the API/events
unhealthy and preventing the dependent gateway from starting.

Reproduction with the actual Django security middleware, strict host
`api.example.test` and HTTPS enforcement:

| Host | Forwarded scheme | Result |
| --- | --- | --- |
| `api.example.test` | `http` | 301 redirect |
| `api.example.test` | `https` | Expected unauthenticated 401 |
| `localhost` | absent | DisallowedHost / 400 |

Required change: establish the public TLS/domain configuration; overwrite
untrusted forwarding headers at the public edge and preserve the trusted
HTTPS scheme at inner hops. Use a configurable allowed health-check host and
a secure, explicitly trusted internal probe strategy. Keep event streaming
unbuffered through every layer. Rerun checks through the actual HTTPS endpoint.

Reference: [Django proxy HTTPS settings](https://docs.djangoproject.com/en/6.1/ref/settings/#secure-proxy-ssl-header).

### 4. The running backend image is behind the source release

The running API image has no `config/live_events.py`, its `requirements.txt`
differs from the checkout, and the main stack has no running `events` service.
The newer SSE implementation was tested in an isolated candidate stack; a Git
push and frontend rebuild do not deploy it to the existing backend containers.

Required change: after the production configuration is complete, build/tag the
final backend release, apply its migrations using migration credentials and
deploy the API, ASGI event service and forecast worker from that same image.
Confirm both health checks and a real event stream through the HTTPS gateway.
Retain the previous release image and a verified database backup for rollback.

## Hosting and operations still to verify

- Configure the actual OVHcloud VPS/domain/certificates, firewall and renewal.
  The local gateway/database exposure is private, but this does not verify the
  intended server's configuration.
- Schedule full database backups outside the VPS, define retention and restore
  a backup into an isolated database. The historical sales SQL export is a
  selected dataset export, not a complete database/server recovery plan
  (`sales/backups/history_sql.py:1`). No full database backup schedule is
  configured in the supplied Compose files.
- Configure log retention and service/error alerts, including last successful
  forecast maintenance and backup runs. A running worker container alone does
  not establish that its latest job succeeded.
- Verify SMTP delivery, reboot/startup recovery and workload capacity on the
  chosen VPS. Previous local 10-user measurements are documented in
  `BACKEND_VALIDATION.md`; they are not an OVHcloud capacity measurement.
- Forecast publication remains subject to the existing source freshness and
  approval gates. A deployment must preserve those states.

## Checks completed in this audit

- 32 focused deployment, Docker health and connection/security regression tests
  passed using a disposable PostgreSQL test database and a test-only fast
  password hasher. Application security settings were not modified.
- Running API/database/cache health passed. The running image's migration
  check passed; the checkout's migration dry run reported no model changes.
- The current runtime's `check --deploy --database default` produced four
  security warnings. The database privilege check independently returned
  `config.E002` when enabled for the actual runtime role.
- Reproduced Compose production-variable omissions and HTTPS/host probe failures.
- Scanned all 30 installed Python packages in the live-events candidate image
  with `pip-audit`: no known vulnerabilities reported and no packages skipped.
  This result covers that Python package snapshot; an OS/container-image scan
  remains part of release verification.
- The earlier 595-test functional regression and isolated live-stream/Docker
  measurements remain documented in `BACKEND_VALIDATION.md`; they were not
  rerun or treated as proof that production configuration is complete.

Public deployment sign-off requires these blockers resolved and Django's
[deployment check](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/#run-manage-py-check-deploy)
run against the actual production settings and runtime database credentials,
followed by HTTPS, SMTP, backup/restore and service recovery verification.
