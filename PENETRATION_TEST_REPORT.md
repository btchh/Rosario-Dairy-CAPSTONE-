# SQL injection and XSS assessment — 2026-10-27

## Result

No SQL injection, authentication bypass through injection, or executable
cross-site scripting was reproduced in the tested paths. Two adjacent issues
remain **open**: authenticated report work amplification (Medium), and malformed
password-recovery input causing a server error (Low). This pass adds verification
tests and documentation; it does not fix these two production-code issues.

Backend tested: `e47687a091bf4ba008f0d0c77b1d34259d44e828`.
Frontend inspected read-only: `3a0be9c98aadcbed3dcac8a63602799df5cf2b50`.

## Scope and isolation

- Source-guided manual SQL-injection and stored/reflected/DOM-XSS probes.
- Actual loopback HTTP requests through Django middleware, routing, JWT
  authentication, serializers, PostgreSQL and response rendering. Admin and
  Staff accounts and records exist only in a fresh disposable test database.
- In-memory test cache and mail backend; no real email, live business-data
  writes, external targets, forecast fitting, cleanup or restore commands.
- Real headless Chromium rendering of actual frontend components with hostile
  fixture strings. The browser harness blocked backend requests.
- Report work amplification measured with database cursors blocked and report
  lookups mocked; no high-volume database load or outage was attempted.

## Findings

### PT-01 — Medium: excessive work in a single historical report

**Access:** authenticated Staff or Admin. **Status:** open.

The monthly report accepts very long custom date ranges. It walks every date
and executes a separate `_sales_summary` aggregation for each calendar week.
The request-count throttle does not limit this per-request work.

This request passes current validation as of the test date:

```http
GET /api/reports/preview/?type=monthly_sales&start_date=1100-01-01&end_date=2026-10-27
Authorization: Bearer <isolated-test-account-token>
```

A safe instrumented execution produced **48,360 weekly output rows** and
**48,362 summary calls**. Each unmocked summary evaluates one SQL aggregate,
so the normal path would execute at least that many aggregate queries plus
other report queries. Both preview and PDF permissions admit Staff and Admin.
An uncached request can occupy application/database resources for a long time;
changing the dates can bypass reuse of a previously cached report. Actual
duration, memory use and service outage were not measured.

Evidence: `reporting/services.py:66` (aggregate), `:242` (monthly report and
weekly loop), `reporting/serializers.py:288` (date-range validation), and
`reporting/views.py:29` / `:48` (preview/PDF permissions).

**Recommended correction:** aggregate time buckets in grouped SQL, bound
interactive output/work, and use an appropriate asynchronous path for oversized
exports. Preserve legitimate historical reporting and existing Staff/Admin
visibility. Add query-budget and large-range regression coverage. An arbitrary
historical year cutoff or weaker throttling is not required.

### PT-02 — Low: public forgot-password input can return HTTP 500

**Access:** unauthenticated. **Status:** open.

An escaped NUL character in the username reaches psycopg2 without input
validation. The adapter rejects it before executing SQL; the forgot-password
view does not catch the resulting `ValueError`.

Reproduced over HTTP against the disposable database:

```http
POST /accounts/forgot-password/
Content-Type: application/json

{"username":"pentest-admin\u0000","email":"admin@example.test"}
```

Observed result: **500**, `text/html; charset=utf-8`, generic server-error page
with debug disabled. This does not require a matching account. The analogous
reset-password request returns **400** but echoes the adapter message,
`A string literal cannot contain NUL (0x00) characters.` Login already rejects
NUL usernames with a clean validation error.

Impact is limited to malformed-request handling and avoidable error/log work;
the existing recovery throttle remains in place. No SQL execution, credentials,
stack trace in the HTTP response, password change, or persistent data corruption
was observed.

Evidence: `accounts/views/forgotpassword_views.py:35` and `:58`, and
`accounts/services/password_reset_service.py:32`.

**Recommended correction:** validate recovery input before database lookup,
including NUL rejection, and return controlled API errors rather than adapter
details. Preserve neutral recovery messages and existing-account compatibility.

## SQL-injection results

Boolean, UNION and stacked-statement payloads were exercised in login usernames,
password recovery, transaction/customer/order IDs, date filters, report type,
revenue period, best-seller limit and forecast parameters. Invalid typed values
returned 400/404 and login attempts returned 401; no injected login received a
token. A SQL UPDATE canary supplied as a customer name remained literal stored
text, and the separate sentinel customer and stock quantity stayed unchanged.
A valid test OTP could not be issued to or consumed through an injected identity.
The payload set included `pg_sleep`; no separate blind/time-differential
SQL-injection measurement was performed.

Source review found ORM parameters or explicitly parameterized advisory-lock
queries in HTTP paths. SQL backup restoration executes generated SQL only in
the offline operator command; no public backup upload/restore route was found.
Offline backup identifiers are quoted and stored values use driver quoting.

## XSS results

- HTTP probes stored script, SVG/event-handler, image/event-handler,
  closing-script and JavaScript-URL strings in test customer data. JSON responses
  used `application/json` with `nosniff`; browsable HTML escaped the strings.
  Reflected report errors did not produce executable markup.
- HTTP inventory/customer PDF downloads remained PDFs served as attachments.
  The inventory PDF was tested with hostile catalog markup and did not create
  active PDF actions or links; the HTTP customer PDF check verified format and
  attachment behavior without a hostile customer fixture.
- Chromium exercised **8 payloads across 9 actual component types (72 cases)**:
  user/customer/category/inventory/order/sale cards, tables, error displays and
  receipt modals. Charts, tooltips, recent orders and eight toast messages were
  checked too. All strings remained text: **zero payload executions, injected
  elements, browser errors or backend requests**. Two positive-control script
  executions confirmed JavaScript was enabled.
- Database-free rendering probes passed **9 email-template cases, 9 generated
  sales PDFs and 9 inventory-ledger payloads**. Django template autoescaping and
  the PDF paragraph escaping retained literal text without active markup/actions.

Stored markup in JSON is not by itself stored XSS: an executable browser sink
is required. No such sink was found in the reviewed application rendering.

## Verification and retained artifacts

`config/test_injection_security.py` adds nine HTTP integration tests. Together
with the 17 existing connection-security tests, **26 tests passed**. The nine
new tests generated **123 loopback HTTP requests**. A separate six-case
malformed-identity diagnostic reproduced PT-02; it is not a passing assertion
that recovery handles all malformed input correctly.

Django system checks, Python syntax for the new module and `git diff --check`
passed. The disposable PostgreSQL database was removed after verification.

The integration suite deliberately clears the isolated cache between some
payloads to test validation independently of rate limiting. Existing
connection-security tests cover throttling and safe cache failures. No throttle
configuration was changed.

Re-run the integration regressions using a development role permitted to create
disposable test databases and a unique `DATABASES['default']['TEST']['NAME']`:

```sh
venv/bin/python manage.py test config.test_injection_security config.test_connection_security --noinput
```

The test class checks the active database name starts with `test_`, binds only
to loopback, and overrides cache/mail. Use Django's test runner; never retarget
the probes at business data.

Private temporary evidence from this run:

- `/tmp/rosario-penetration-sq9l_49y/`: isolated settings, final test log,
  request/result JSONL with JWTs redacted, and malformed-input evidence.
- `/tmp/rosario-resource-probe-87HRKcVA/`: standalone zero-database reproduction
  script, instructions and call-count evidence for PT-01.
- `/tmp/rosario-xss-browser-sb5284xi/`: browser/component fixtures, rendered DOM,
  results, and database-free email/PDF reproduction artifacts.

These temporary artifacts may disappear after a restart; the report findings
and HTTP regression module are retained in the backend repository.

## Limits

This was a targeted local assessment, not a complete deployed-system security
certification. The browser tests used component fixtures, not full authenticated
end-to-end journeys through the deployed frontend. Hosting, TLS/proxy behavior,
production response headers/CSP, infrastructure, Windows behavior and sustained
concurrent load were not tested. Intentional local-development defaults were
not classified as production vulnerabilities. No production application code,
frontend source, database schema or business data was changed in this pass.
