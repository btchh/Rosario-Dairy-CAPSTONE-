# Backend deployment readiness — 2026-11-01

**Release `release-2026.11.01` is finalized for deployment preparation.** Public
production acceptance still depends on target-host setup and verification.
The current local demo passed the checks below; pagination is deferred by the
user and forecast limitations remain. See [RELEASE.md](RELEASE.md) for the package.
See [DEPLOYMENT_FEATURE_AUDIT.md](DEPLOYMENT_FEATURE_AUDIT.md) for the feature
matrix, actual HTTP/browser coverage and untested acceptance paths.

The subsequent [source parity review](BACKEND_FRONTEND_PARITY.md) identifies
backend-only flows and partial UI controls. Those capabilities must not be
treated as completed frontend features; the review preserves server pagination.

## Current release and verified behavior

Frontend `5e390189` and backend application source `df6f732a` are deployed
locally. `inventory.0013_stock_adjustment_submission` is applied. Stock-out
request IDs/recovery cover outflows and expired disposal. Idle logout uses
900 seconds with a 60-second warning; real interaction extends the session,
while background reads and token refresh do not.

The seven-app backend regression ran 991 tests: **990 passed and one Windows-only
check skipped**. Twelve real HTTP workflow groups passed in an isolated restored
database. Chromium checks passed for all 17 main role screens, actual admin/staff
login/logout and six PDF downloads. These checks do not establish that every UI
form or production dependency was tested. The broader audit used frontend
`840f08ea`; the subsequent `5e390189` rollout passed 662 frontend tests,
TypeScript, the Docker production build and asset/proxy checks. That small
Staff Orders update did not repeat every authenticated browser workflow.

API/events/frontend are healthy and the forecast worker is running. Full private
pre-release backups restored successfully in isolated PostgreSQL. All 19 existing
business tables were unchanged through rollout/migration checks. Gmail
authentication passed locally and the user confirmed an OTP arrived. The audit's
recovery-email workflows used file capture and sent no external email. Release
image identities and rollback evidence: [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md).

## Application work and acceptance still open

- **Deferred frontend server pagination:** Orders, staff history, admin history/encoding
  search/sort and staff order-count reads still have full-history paths. This
  is retained for later work in
  [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md). Orders issued ten list requests in
  this audit. The user's longer POS-to-admin delay was not reproduced and must
  not be claimed fixed.
- **Forecast data/approval:** weekly/monthly saved points are available, yearly
  coverage partial, several views report stale source data, and all retain
  `production_approved=false`. Successful jobs/read endpoints do not certify
  accuracy or make missing historical records complete.
- **Browser acceptance:** many write paths were verified over HTTP rather than
  through forms. Full form journeys, mobile layouts, PDF visual quality and
  final-host cross-account updates remain to verify. The feature audit records
  the actual scope for each area.

## Production configuration exists; target deployment is outstanding

Use [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) and the standalone
`compose.production.yaml`. It provides separate administrator/migration/runtime
roles, startup deployment checks, HTTPS-aware internal proxies and health probes.
An earlier isolated rehearsal passed provisioning, migrations, preflight,
restricted-role CRUD/DDL checks, service health and live updates. This does not
verify the eventual public host.

The running local stack still uses `DJANGO_ENV=development`, has no HTTPS
redirect, secure cookies or HSTS, and connects with an elevated PostgreSQL role.
Its deployment check reports `security.W004`, `W008`, `W012` and `W016`.
Do not publish that local configuration unchanged.

Before accepting public traffic:

1. Finalize the host/domain; apply production configuration, restricted database
   roles, HTTPS, trusted forwarding, firewall and certificate renewal. Run
   deployment checks with the actual runtime credentials.
2. Transfer current data with verified ownership, migrations and restore checks.
   Retain rollback images and a verified full backup.
3. Confirm password-recovery delivery from that host. If choosing DigitalOcean,
   resolve its default SMTP block before relying on Gmail SMTP.
4. Schedule off-host full backups with retention and a tested restore. Configure
   service/error, backup and forecast-job monitoring.
5. Verify public HTTPS browser flows, event reconnects, idle continuation/logout,
   reboot recovery and expected concurrent load on the chosen VPS.

HSTS subdomain/preload settings remain explicit domain-policy decisions. The
original 2026-10-30 findings and subsequent local fixes are retained in the
[previous readiness record](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/0cc5d0c4/BACKEND_DEPLOYMENT_READINESS.md).
They should not be read as unresolved repository defects after the separate
production configuration was added.
