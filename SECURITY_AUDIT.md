# Backend connection, logic and security audit — 2026-10-23

Scope: accounts, inventory, sales, reports, forecasting, settings, PostgreSQL,
cache behavior and the installed Python dependencies. Tests use the separate
Django test database; production sales and forecasts were not changed.

## Confirmed findings and changes

| Finding | Change |
| --- | --- |
| A cache connection/read/write failure escaped DRF rate limiting as an unhandled error. | Both IP and account throttles now return a safe 503. They fail closed, preserve 401/403 precedence and resume normal limits after recovery. No business action runs during the failure. |
| Database operational/interface errors had no API-specific availability response. | A narrow DRF handler logs the server error and returns a safe 503 without exposing connection details or retrying operations. Other programming errors remain visible as errors. A failed write response does not prove that a remote commit failed; clients must check transaction status before resubmission. |
| Staff customer summaries counted hidden/inactive-category and mixed receipts excluded from their receipt history. | Counts and latest-sale dates now use the same visible, non-voided receipt set as history. Admin summaries retain their full scope. Warm-cache and category-visibility changes are covered. |
| Empty JSON arrays/null/scalars could start a forecast refresh despite the empty-object contract. | Refresh requires an empty object; invalid bodies return 400 before enqueueing. |
| The dependency scan reported 38 advisories across two installed packages. | Updated DRF 3.18.0 to 3.18.3 and Pillow 12.3.0 to 13.0.0; both are pinned in requirements. The follow-up scan found zero known advisories in all 32 installed packages, with no skipped packages. |
| Deployment instructions referenced the removed `evaluate_forecast` command. | Updated the example to the tracked midnight Asia/Manila maintenance command and deployment timer. |
| Batch numbers used UTC while counters used the business month, allowing old-month prefixes with reset counters after local midnight. | Product and ingredient creation capture one business date and pass it to both numbering helpers. Month/year boundaries and a clock crossing midnight after allocation are tested. |

DRF's [official release notes](https://www.django-rest-framework.org/community/release-notes/)
identify the content-negotiation, non-text-charset and token-proxy security fixes;
3.18.3 also reverts an accidental API change. Pillow's
[13.0.0 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)
list the security fixes and compatibility changes. Advisory counts describe the
installed dependency versions, not proof that every vulnerable path was exposed
by this application.

## Verification

The baseline full suite passed **629 tests** in 495.044 seconds. Thirteen added
checks pass for Redis/file-cache connection failures, safe database error
responses, mutation prevention, recovery and existing 429 limits, permission
precedence, malformed refresh bodies, staff summary visibility/cache invalidation,
oversized Accept tokens, non-text JSON charsets and batch month/year boundaries.

The post-security-fix complete suite passed **640 tests** in 473.513 seconds.
The final batch-date fix then passed **304 inventory, sales and audit tests**
in 19.076 seconds, including both new boundary checks. Password hashing is
accelerated only inside the test process; deployment password settings are not
changed. `pip check` reports no broken requirements. Django system checks and
migration drift checks are included in the suite.

Audit logs (temporary, may be removed by later cleanup):

- `/tmp/rosario-full-backend-audit-tests.log`: baseline full suite.
- `/tmp/rosario-audit-fixes-tests.log`: added regression checks.
- `/tmp/rosario-full-backend-audit-final.log`: complete post-fix suite.
- `/tmp/rosario-audit-boundary-integration.log`: final batch-date integration checks.
- `/tmp/rosario-dependency-audit-fixed.json`: dependency results.
- `/tmp/rosario-bandit-audit.json`: static-analysis candidates.

## Connection and deployment limits

- The configured local PostgreSQL connection passed a query probe. The configured
  **file cache** passed a unique-key write/read/delete probe. Actual production
  Redis connectivity was not exercised; Redis failure behavior was injected in
  tests. SMTP is currently the console backend, so this audit did not send email
  or verify external SMTP credentials/TLS.
- The current local environment runs in development mode with DEBUG and the
  development secret. Deployment checks report the expected development-only
  HTTPS/cookie/secret warnings. Do not expose this profile as production.
- An isolated production-settings check, using generated/dummy environment values
  and no external connections, raised only the two optional HSTS subdomain and
  preload warnings. These depend on domain ownership and HTTPS coverage; the
  audit did not enable them or certify a real hosted deployment.
- The current local database connection uses a superuser role. Production needs
  a separate least-privilege application role; backup/restore operations needing
  database creation should use a separate operational role. Existing roles were
  not modified during the audit.
- Bandit produced 15 heuristic findings (six medium, zero high). The reviewed SQL
  identifiers come from Django metadata and are quoted; values use parameters or
  `mogrify`. The forecast child uses fixed argv and no shell. PDF text uses XML
  escaping, not XML document parsing. Other flags concern development defaults,
  throttle-rate strings, or non-security synthetic test-data randomness. No SQL
  injection or arbitrary command execution was confirmed at those locations.

Passing tests and a clean dependency feed are bounded evidence, not a guarantee
against every security defect. No live load attack, external penetration test,
network failover or deployment-infrastructure audit was performed.
