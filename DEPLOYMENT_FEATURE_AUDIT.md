# Deployment feature audit — 2026-11-01

**Decision: public deployment is not signed off.** The current local demo passed
the checks below. Production hosting and the acceptance gaps below still require
work before public sign-off. Pagination was subsequently accepted as deferred
for the finalized release; see [RELEASE.md](RELEASE.md).

## Scope and evidence

Backend application source `df6f732a` (release documentation `0cc5d0c4`), frontend
`840f08ea`, and their current built Docker images were reviewed. No application
code changed during this audit. Business writes used a disposable Docker project
restored from the private pre-release backup. Live configuration/forecast checks
were read-only. Test email was file-captured; no external email was sent.

The earlier six-app regression had 756 tests: 755 passed and one skipped. It
excluded forecasting's 235 tests. Saying pagination was the only open frontend
handoff request did not establish that every feature, UI form or production
dependency had been verified.

All **991 backend tests ran: 990 passed and one skipped**, in 621.972 seconds,
against disposable PostgreSQL and dedicated Redis for Redis-specific tests.
The skip was the Windows-only private-backup ACL check on this Linux host.
Test-only fast hashing and in-memory mail/cache did not change live settings.

| App | Tests run |
| --- | ---: |
| accounts | 109 |
| config | 181 |
| sales | 214 |
| inventory | 136 |
| reporting | 76 |
| systemsetting | 40 |
| forecasting | 235 |

Frontend **657 tests, TypeScript and production build** passed during the
preceding rollout; they were not rerun during this audit. Release image
identities and verified backups are in [BACKEND_VALIDATION.md](BACKEND_VALIDATION.md).

## Feature coverage

All **12 HTTP workflow groups passed** through the built frontend proxy and
real backend, with actual logins and isolated fixtures. HTTP submissions do
not establish that every corresponding browser form works.

| Feature | Actual HTTP verification | Backend regression examples | Browser scope / remaining acceptance |
| --- | --- | --- | --- |
| Login, roles and sessions | Admin/staff login and authorization, rotating refresh without idle extension, explicit activity extends expiry | `accounts/tests.py`, `accounts/test_idle_sessions.py`, `config/test_connection_security.py` | Both login/logout forms submitted. Warning, continuation, background reads and expiry checked during preceding rollout, not repeated here. |
| Users, profile and recovery | Admin registration/update/deactivation, duplicate guard, profile edit, captured OTP reset and invalid-code rejection, password/email change and token revocation | `accounts/tests.py`, `accounts/test_audit_hardening.py`, `accounts/test_smtp.py` | User Management and Settings loaded. Full recovery/profile form journeys and VPS delivery remain. |
| Categories, products and batches | Category/product/two-batch creation, expiry/cost inputs, hidden-category visibility and staff management denial | `inventory/tests.py`, `inventory/test_product_only.py`, `inventory/test_audit_hardening.py` | Inventory and Categories loaded. All create/edit/hide forms were not submitted. |
| POS checkout | Cash total/change, earliest-expiry deduction, keyed replay and oversell rejection | `sales/tests.py`, `config/test_backend_fixes.py` | Staff POS loaded. Full browser cart/checkout/receipt journey remains. |
| Manual sales and payment | Effective sales date/encoder, keyed retry, payment updater, paid finality/conflict response | `sales/tests.py`, `sales/test_history_api.py` | Sales Encoding/History loaded. Encoder/updater display and paid locks checked during preceding rollout. All encoding/edit forms were not repeated. |
| Orders and cancellation | Fulfilled online order/retry, staff cancellation denial, admin cancellation restores stock, repeat cannot restock twice | `sales/tests.py`, `sales/test_history_api.py` | Both Orders screens loaded. Browser cancellation journey and full-list performance remain. |
| Stock out, count and disposal | First submission/replay/conflict, physical count correction, full expired disposal and replay after exhaustion | `inventory/test_adjustment_idempotency.py`, `inventory/tests.py` | Inventory loaded. Real frontend service lost-response/reload recovery and concurrency checked during rollout; all modal interactions not submitted here. |
| Customers and histories | Create/edit, exact totals, void exclusion, paged lists/summaries, linked-customer deactivation/reactivation | `sales/test_customer_totals.py`, `sales/test_history_api.py`, `reporting/test_customer_accounts.py` | Customers and histories loaded. Filter/formatting interactions and remaining pagination require frontend acceptance. |
| Settings, alerts and notifications | Business identity, stock/expiry thresholds, notification preferences/inbox and staff write denial | `systemsetting/tests.py`, `systemsetting/test_audit_hardening.py` | Settings loaded. Every settings tab/preference/inbox interaction was not exercised. |
| Reports and exports | Six previews/PDF endpoints, invalid range rejection and staff forecast-report denial | `reporting/test_complete_reports.py`, `reporting/test_report_ranges.py`, `reporting/test_pdf_pagination.py` | Six Download PDF buttons clicked; valid downloads/text checked. Visual/print layout remains unreviewed. |
| Forecasts and jobs | Weekly/monthly/yearly × combined/regular/feeding reads, supporting model/evidence reads, invalid period rejection and queued refresh | `forecasting/test_graph_readiness.py`, `forecasting/test_graph_origins.py`, `forecasting/test_refresh_jobs.py`, `forecasting/test_schedule.py` | Dashboard loaded. Two isolated refresh jobs completed. Endpoint success does not certify completeness or accuracy. |
| Live updates and dashboards | History/summary/alert/graph reads succeeded; prior controlled events measured through gateway and both frontend proxies | `config/test_live_events.py`, `config/test_cache_consistency.py` | Dashboards loaded. Exact reported POS-to-admin delay not reproduced; final-host background-tab/reconnect scenario still needs acceptance. |

Fixtures included a PHP 226.50 POS sale with PHP 73.50 change, a PHP 80.00 manual
receipt paid by staff, and PHP 306.50 customer history after excluding a cancelled
order. Stock checks compared both batches and cancellation/disposal replay.

## Browser coverage

Chromium used the current built frontend and isolated restored backend. All
**22 recorded checks passed**, with no observed JavaScript runtime exceptions or
HTTP 500s: 17 role screens, four login/logout checks and one six-download group.

| Role | Screens loaded |
| --- | --- |
| Admin | Dashboard, Inventory, Categories, Orders, Customers, Sales History, Sales Encoding, Reports, User Management, Settings |
| Staff | Dashboard, Point of Sale, Orders, Inventory, Sales History, Sales Encoding, Settings |

Downloaded customer, daily/weekly/monthly sales, inventory and SARIMA forecast
PDFs all had valid structure, extractable text and configured test business
identity. Inventory spanned 24 pages. Visual layout, print clipping, responsive
layouts, accessibility, every modal and every recovery interaction are not
signed off by these checks.

## Remaining findings and release checks

1. **Apply production configuration on the actual host.** The live local
   runtime is `development`, with HTTPS redirect, secure cookies and HSTS off,
   and all five elevated PostgreSQL role flags enabled. Deployment checks report
   `security.W004`, `W008`, `W012` and `W016`. Separate production Compose,
   restricted-role provisioning and HTTPS-aware proxies already exist and were
   rehearsed earlier in isolation. Follow [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md);
   do not publish the local stack unchanged.
2. **Finish frontend server pagination.** [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md)
   lists the exact paths/contract. Orders issued ten list requests per role
   screen. One admin load settled in 15.374 seconds while regression/forecast
   work was running; this is not a target-VPS benchmark. Staff history and
   several search/sort/dashboard paths still fetch full histories. The user's
   longer Sales History delay is not resolved by controlled event timings alone.
3. **Review and preserve forecast limitations.** Read-only live checks found
   seven of seven weekly/monthly points available in each view. Yearly coverage
   was combined 3/5, regular 4/5 and feeding 3/5. All views reported
   `production_approved=false`. Combined/feeding freshness was stale; regular
   monthly/yearly was current and regular weekly stale. The latest live refresh
   succeeded. Refreshing cannot supply missing records or confer approval.
   Altered clone fixtures produced different availability and must not be
   presented as the live dataset's status.
4. **Verify hosting and operations.** Finalize VPS/domain/TLS/firewall, transfer
   current data with verified ownership, test email delivery, off-host scheduled
   backup/restore, monitoring, reboot recovery and expected concurrent load.
   Local Gmail authentication and the user's local OTP do not prove VPS mail
   delivery. If DigitalOcean is chosen, resolve its
   [default SMTP port block](https://docs.digitalocean.com/support/why-is-smtp-blocked/)
   before relying on the existing Gmail SMTP configuration.
5. **Complete the untested browser journeys above.** Full forms, mobile layouts,
   PDF visual quality and the user's cross-account update scenario remain
   acceptance work. Screen loads, API workflows and regression counts provide
   different evidence and are not interchangeable.

## Evidence and disposal

Sanitized summaries: [validation/deployment-audit-20261101.json](validation/deployment-audit-20261101.json).
Temporary diagnostic evidence on the audit host:

- `/tmp/deployment-feature-audit-backend-20261101.log`
- `/tmp/feature-audit-http-results.json` (final combined 12-group result)
- `/tmp/deployment-feature-audit-security-20261101.log`
- `/tmp/deployment-feature-audit-browser-20261101.log`
- `/tmp/deployment-feature-audit-pdf-20261101.json`
- `/tmp/deployment-feature-audit-live-forecast-20261101.json`
- `/tmp/deployment-feature-audit-live-deploy-check-20261101.log`

Disposable services/volumes, private credentials/tokens and downloaded
business-data PDFs are removed after checks. The local demo stack and original
private release backups are retained.
