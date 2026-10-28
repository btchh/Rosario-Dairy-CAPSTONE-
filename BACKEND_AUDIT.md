# Backend audit — 2026-10-26

Reviewed backend commit `ebd2521b`, the two supplied screenshots, and the later
pasted frontend audit. Application code was not changed during that initial
review. The original findings below describe the pre-fix behavior and distinguish observed behavior from policy decisions and
deployment work. Severity reflects potential impact, not proof of exploitation.

## Forecast reliability and readiness fixes — 2026-10-28

Two confirmed backend defects were corrected without changing forecasting
algorithms, candidate definitions, thresholds, database schema or permissions:

1. The centered graph ignored explicitly reviewed zero-sales months, although
   the primary monthly engine included them. A valid primary SARIMA fit could
   therefore be discarded in favor of a provisional fit. Regular monthly graph
   inputs now include only exact-source, valid confirmed-zero coverage records.
   Unknown months remain null, stale attestations remain ineffective, and
   corrected inputs invalidate the saved graph identity through existing hashes.
2. Portable SARIMA validation accepted invalid scales/parameters and could
   crash on malformed history. It now verifies finite numeric history and
   parameters, positive finite scale, integral matching training counts, exact
   specification parameter order/count and nonnegative innovation variance.
   Invalid states block approval and filtering. Malformed hashes and null/non-
   mapping artifacts no longer crash readiness metadata; malformed graph
   checksums are safely withheld. Valid saved fits retain their predictions.

The graph endpoint also adds `readiness` and per-view/per-point `model_details`.
Legacy `status=ready` continues to mean a current saved graph exists; the new
availability field can still be `unavailable` when there are no predictions.
Projection availability never establishes production approval. Model details
identify nonseasonal ARIMA selections, observed/missing periods, training dates,
evaluation sample sizes and forecast origins. Yearly points disclose their
different information cutoffs; monthly tests are not labeled yearly accuracy.
Original issued predictions are retained without attributing a reconstructed
model's diagnostics to them. Existing values and response fields are preserved;
frontend adoption is documented in `FRONTEND_HANDOFF.md`, with no frontend edits.

Read-only investigation of the current local demonstration database found:

- The regular monthly run is current through September 2026. Its 12-month
  retrospective WAPE is 5.9045%, versus 11.039% for its selected baseline.
  These results include generated 2026 outcomes and are demonstration evidence,
  not validation against original 2026 invoices. The earlier original-data fit
  scored 10.6433%, versus 11.381% for its baseline.
- Feeding has 19 observed months across 23 calendar months. February, May,
  June and July 2025 are unknown, not confirmed zeroes. Its selected AR(1)
  coefficient is approximately 0.0157, explaining forecasts approaching PHP
  3.63 million. Seasonal training eligibility is unmet; no artificial seasonal
  curve or relaxed history requirement was introduced.
- Strict weekly retrospective WAPE is 8.2603%, worse than its 6.1348% baseline.
  This result does not justify a claim that SARIMA improves weekly accuracy.
- `research_only` remains correct: there are no reviewed coverage months,
  prospective issued monthly acceptance forecasts, prospective scores or
  deployments. Source review and 12 consecutive reviewed prospective outcomes
  remain required. Generated transactions cannot supply genuine prospective
  evidence, and no issues, reviews or approval records were fabricated.

Verification: the complete forecasting suite passed **167/167 tests** in 403
seconds; the final focused run passed **32/32 tests** in 5.6 seconds, including
the final malformed-metadata regressions. These runs overlap. Both used fresh
disposable PostgreSQL databases and isolated caches, removed after completion.
Pure artifact tests also verify all five actual fitted configurations retain
their JSON-round-trip predictions with optimization prohibited on reads.
Django checks, Python syntax, migration consistency/status and whitespace checks
pass; no migrations were applied. Read-only application checks preserve every
saved graph prediction and fit no models. Six authenticated HTTP checks cover
all three periods directly and through the running frontend proxy. The local
backend was restarted and its API cache retired; frontend source is unchanged.

Pre-push frontend review: remote `d4d35f44` implements the earlier connector,
registration, report-date, receipt-label and automatic-refresh handoff items.
No compatibility blocker was found with these additive backend fields. An
isolated snapshot builds successfully; all 365 frontend tests pass with native
Node web storage disabled. Default Node 26 exposes an existing test-isolation
issue: seven POS tests fail on both old/new commits because test setup does not
clear native sessionStorage. Individual/order-dependent reproductions confirm
the cause; production duplicate-sale guards were preserved. The storage-test
follow-up and the still-pending model/readiness display are documented in
`FRONTEND_HANDOFF.md`. The running local frontend remains at the earlier commit;
this backend-only pass inspected the update without pulling or editing it.

## SQL injection and XSS assessment — 2026-10-27

See [PENETRATION_TEST_REPORT.md](PENETRATION_TEST_REPORT.md) for the targeted
local assessment of backend `e47687a0` and the read-only frontend rendering
review. No SQL injection or executable XSS was reproduced. Two adjacent findings
remain open: **Medium** authenticated report work amplification, and **Low**
malformed forgot-password usernames returning HTTP 500. This assessment adds
HTTP regression coverage and documentation; production fixes are not included.
The tests use disposable PostgreSQL data; the excessive report workload was
measured with database access blocked. Hosting and deployed end-to-end behavior
remain outside this local test's evidence.

## Full backend deployment-preparation review — 2026-10-27

This pass reviews the complete backend and preserves the existing working tree,
frontend, database schema, role permissions and intentional local development
configuration. All writes, fits and restore tests run only in fresh disposable
PostgreSQL databases. Live inspection uses database-enforced read-only
transactions. No deployment, live cleanup, retraining or data correction was run.

Seven confirmed issues were fixed:

| Issue | Previous behavior | Corrected behavior |
| --- | --- | --- |
| Report comparison calendar underflow | A valid-looking early/long sales range could overflow its preceding comparison dates and return 500 from preview/PDF. | Shared query validation returns 400 when the required comparison cannot be represented; supported historical ranges remain valid. |
| Inventory expiry calendar overflow | Admin settings accepted extreme day thresholds that subsequently crashed stock alerts and inventory reports. | New settings reject unrepresentable ranges; shared cutoff calculation safely saturates at the last representable date for legacy values and date advancement. |
| Long inventory ledger PDF remarks | Ordinary accumulated stock-adjustment reasons could make one table row taller than a page and raise a ReportLab layout error. | Ledger rows can continue across pages, preserving complete remarks and repeated headers. |
| Lossy record ID coercion | Fractional/boolean batch or checkout-customer IDs could be coerced to another integer record before a mutation. | Those malformed IDs return 400 before lookup/mutation; positive integer strings and supported empty optional fields remain compatible. |
| Concurrent admin mutation deadlock | Different admin targets were locked before the common active-admin set, allowing PostgreSQL lock inversion. | Both mutation paths lock active admins in primary-key order before the target; the last-active-admin restriction remains enforced. |
| Logout availability error handling | A database outage was caught as an invalid refresh token and returned 400. | Expected invalid tokens retain 400; database outages reach the existing safe 503 handler without retrying revocation. |
| Runtime preferences cache race | A cold reader could commit late and overwrite newer committed business preferences for five minutes. | Reads capture a cache namespace before loading data; update/delete commits retire that namespace, so late readers cannot overwrite current preferences. Initial singleton creation retains its namespace to preserve warm-cache query budgets. |

Regression tests reproduce the original calendar errors, malformed-ID stock
mutation, PostgreSQL admin deadlock and late-reader cache overwrite. PDF tests
retain all 50 distinct adjustment markers across multiple pages. Existing query
budgets were retained. The cold singleton and concurrent cache cases both have
regressions; no query-count limits were increased.

Final complete suite: **756 tests in 384 seconds — 755 passed, one skipped,
zero failures/errors**. The skip is native Windows ACL verification on this
Linux host. This includes 23 new regressions. All test databases and their
parallel clones were removed after the run. Completed component runs also
include 101 account/security tests, 46 inventory/sales tests, 30 reporting tests
and a settings/sales run of 248 tests
(247 passed, one native Windows ACL test skipped). These runs overlap and must
not be added together. The original full run was interrupted by the host crash;
five abandoned test databases were removed after confirming no active sessions.
A resumed run exposed an initial cache-warmup regression; it was corrected
without increasing any existing query budgets before the final complete rerun.

Django system checks, migration consistency/status, Python syntax (355 files),
dependency consistency and whitespace checks pass. An isolated-tool `pip-audit`
scan of all 32 installed backend packages reports zero known advisories and no
skipped packages. This is advisory coverage, not a security guarantee. A
production-mode configuration-only check with generated test credentials has
zero errors and two HSTS policy warnings (`security.W005`, `security.W021`).
It does not verify a real hosting environment or connect to its services.

Read-only live checks found no invalid product/ingredient batch quantity bounds
or unvalidated database constraints. One active admin is present. Saved forecast
object counts match the previous audit; the `stale_data` finding and data/
coverage/prospective-evidence requirements below remain unchanged.

Valid frontend payloads and response fields are unchanged. The intentional error
corrections are 400 for the newly rejected malformed IDs/date ranges/thresholds,
and 503 for logout database unavailability. No frontend change is required for
valid existing requests. Actual hosting/HTTPS/proxy behavior, runtime database
grants, Redis/SMTP operations, scheduler/recovery, staging journeys and native
Windows ACL verification still require validation on the intended deployment
host. Localhost/development defaults are not treated as defects in this review.

Files changed during this full review (earlier working-tree edits retained):

- `accounts/services/admin_service.py`, `accounts/views/logout_views.py`,
  `accounts/test_audit_hardening.py`.
- `config/api_inputs.py`; `inventory/serializers/fefoconf_serializer.py`,
  `inventory/services/eligibility.py`, `inventory/services/stock_check_service.py`,
  `inventory/views/stockadjustment_views.py`, `inventory/views/stockcount_views.py`,
  `inventory/test_audit_hardening.py`.
- `reporting/serializers.py`, `reporting/services.py`, `reporting/pdf.py`,
  `reporting/test_audit_hardening.py`; `sales/views/checkout_views.py`.
- `systemsetting/runtime.py`, `systemsetting/test_audit_hardening.py` and this
  audit record.

Local settings, deployment environment examples, requirements, frontend and
frontend handoff content are unchanged by this full review. No model or
migration changes were made, and no live migrations were applied.

Evidence is retained under `/tmp/rosario-full-backend-zt6grm_f/`, including
`start-hashes.json`, `runtime-before.log`, `runtime-related-final.log`,
`cache-sales-final.log`, `readonly-integrity.json`, `python-advisories.json`,
`production-config-check.log` and `verified-full-tests.log`. The earlier full
logs retain the interrupted run and the corrected cache-query finding.
Component logs:
`/tmp/rosario-auth-review-a0sZKnTA/hardening-tests.log`,
`/tmp/rosario_sales_review_s3o0y6vu/hardening-final-tests.log` and
`/tmp/rosario-report-audit-phvlEy/tests.log`.

## Targeted local maintenance verification — 2026-10-27

This later pass follows Jay Mark's backend-only scope. Localhost and development
settings are intentional. No configuration, frontend/handoff, models, migrations,
authentication, Staff/Admin permissions, forecasting algorithms or HTTP API
contracts were changed during this pass. Earlier working-tree edits were
preserved. Production provisioning remains a separate hosting task.

**Confirmed fixes and backup security.** The previously applied explicit UTF-8
SQL restore/manifest handling and simulated-cp1252 regression were retained and
verified. The regression additionally checks literal Unicode in the SQL, exact
decompressed bytes, source-row preservation, restored fingerprints, and POSIX
permissions on the directory, SQL, gzip and manifest. Backup content and stored
sales values were not rewritten.

The Unix 0600 assertion was an OS-specific assumption; it does not measure
Windows ACLs. Both backup paths now share private directory creation. POSIX
keeps 0700 directories/0600 files. On Windows the helper checks Python 3.12.4+
and `FILE_PERSISTENT_ACLS` on the target volume before creating output, then
uses Python's protected, inheritable 0700 directory ACL for owner, SYSTEM and
administrators. Existing output directories remain rejected. An unsupported
runtime/filesystem or failed volume check aborts before writing sensitive data.
No permissions were broadened or backup-serving routes added.

Native Windows tests read the folder/file ACLs through Windows security APIs,
checking protected directory ACLs and the absence of unrelated allowed
principals. These tests are present but **not executed on this Linux host**.
Mocked failure guards and real POSIX permissions pass. Native Windows backup/
restore and ACL verification must still be run on the intended Windows machine;
this report does not claim that its effective ACLs have been verified remotely.
The implementation uses documented
[Python directory ACL behavior](https://docs.python.org/3.12/library/os.html#os.mkdir),
[CPython's protected/inheritable descriptor](https://github.com/python/cpython/blob/3.13/Modules/posixmodule.c)
and [Windows filesystem ACL capability flags](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationw).

**SARIMA investigation.** Read-only, repeatable-read PostgreSQL verification on
2026-10-27 (Asia/Manila) confirms `stale_data` is expected:

- Eligible observed sales span 2022-03-21 through **2025-12-31**; the contiguous
  monthly fitting series contains 36 months, January 2023–December 2025.
- Current-source monthly run **4**, engine `regular-monthly-sarima-v2`, matches
  the 2026-10-01 snapshot cutoff but is trained only through 2025-12-31. Its next
  saved research target is **2026-01-01**, not October 2026. A recent refresh or
  cutoff date is not evidence that underlying sales are recent.
- Gate reasons are `stale_training_data`, `unverified_ledger_coverage` and
  `needs_12_prospective_reviewed_months`. There are zero coverage attestations,
  issued monthly forecasts, monthly forecast scores and active deployments.
- Saved retrospective monthly evidence covers 12 months in 2025: WAPE 10.6433%
  versus the previous-month baseline's 11.3810%. This is historical research,
  not current predictive accuracy. The separate weekly research is also stale;
  its 13-period historical WAPE is 26.8135%, worse than baseline 20.2992%.
- Centered real graph runs 43/44/45 are ready for 2026-10-27, with 7/7/5 points
  per view for weekly/monthly/yearly. Weekly/monthly evaluations are empty;
  yearly historical evaluations contain one period for Combined/Feeding and
  two for Regular. Graph availability is distinct from forecast approval.
- There are 51 unclassified invoices requiring source review where relevant.
  No fit was performed; fitting was explicitly blocked in the read-only probe.
  Saved forecast object counts were unchanged during and after verification.

No forecasting implementation defect was confirmed by these checks. For defense,
explain the available historical evidence and show unavailable current forecasts
honestly. A current October monthly fit needs genuine reconciled history through
September 2026; missing months must not be assumed to be zero. Review source
classifications and coverage against original ledgers, then arrange offline
maintenance as separately authorized data preparation. Approved publication also
requires 12 consecutive recent reviewed **prospective** monthly outcomes, WAPE
below 15% and no worse than the baseline, plus the existing remaining gates.
Retrospective imports cannot create that prospective record. Do not backdate
issues or fabricate predictions to prepare for defense.

**Report refresh.** No backend endpoint defect was found. Regression tests verify
per-account and global once-per-minute limits, 429/`Retry-After` on repeats,
recovery after 61 simulated seconds, safe 503 responses for account-throttle
cache reads/writes and global-cooldown cache failure, and lock release after
failure. Two checkouts commit before a subsequent refresh is rejected; a fresh
database connection verifies that both sales and stock deductions persist.
The same persistence is verified after refresh returns 503. Rate limits and
refresh response contracts are unchanged.

**Files changed in this pass:** `sales/backups/security.py`,
`sales/backups/history_sql.py`, `sales/management/commands/remove_dummy_data.py`,
`sales/test_backup_security.py`, `sales/test_history_sql_backup.py`,
`reporting/test_refresh_isolation.py`, `README.md` and this audit document.
Existing edits in `sales/test_dummy_cleanup.py` and other files were preserved.

**Verification:**

- Initial targeted run: 55 tests, **54 passed, 1 skipped, 0 failures/errors**,
  including Unicode restore, cleanup preservation, refresh isolation, cache
  outages, permissions, authentication/concurrency and SARIMA stale-data gates.
- After final backup guard/test updates: 18 backup/report tests, **17 passed,
  1 skipped, 0 failures/errors**. The skip is native Windows ACL verification.
- Final SQL-content/permission update: both SQL backup tests passed. Counts
  above overlap; they are separate runs, not additional unique coverage.
- Django system check: no issues. `makemigrations --check --dry-run`: no model
  changes. `migrate --check`: no unapplied migrations; no application migrations
  were applied. Python syntax: all 351 files passed. Whitespace checks passed.
- All write/restore tests used fresh disposable PostgreSQL databases and an
  isolated in-memory cache. The test and nested restore-verification databases
  were removed. Live inspection used database-enforced read-only transactions.
- The complete backend suite was not rerun for this targeted pass. Native
  Windows ACL/OS restore checks cannot run here; live cleanup/restore was not
  attempted because it would violate the requested data boundary.

Evidence: `/tmp/rosario-targeted-maintenance-5nh13jb2/forecast-readonly.json`,
`tests.log`, `final-backup-report-tests.log` and `final-sql-tests.log` in the same
private temporary directory. Remaining deployment work is host-specific HTTPS,
hosts/origins, least-privilege credentials, Redis, SMTP, scheduler/recovery,
backup ACL/restore verification on that host and staging workflows. Local
development configuration is not a deployment defect in this maintenance pass.

## Deployment/backup audit follow-up — 2026-10-27

Scope: remediate backend findings in the subsequently pasted readiness audit
and update `FRONTEND_HANDOFF.md`. The sibling frontend repository is unchanged.

- SQL restore verification now reads exported SQL explicitly as UTF-8. Its
  existing PostgreSQL Unicode/quotes/null/money round-trip regression now forces
  cp1252 for unspecified `Path.open()` encodings, so the Windows-default failure
  can be reproduced on Linux. SQL manifests also use explicit UTF-8.
- Cleanup's functional backup/deletion regression still runs on Windows. Its
  POSIX directory/file permission assertions are separate and explicitly skip
  unsupported platforms. This is not Windows ACL verification; deployment-host
  ACL validation remains outstanding.
- Production settings require explicit database connection fields, reject the
  development database users/password, wildcard hosts, insecure configured
  browser origins, disabled HTTPS redirects/secure cookies and nonpositive
  HSTS. Proxy-header trust requires explicit opt-in. Existing Redis, SMTP,
  secret-key and DEBUG production guards remain in force.
- `check --deploy --database default` adds a read-only production role check for
  superuser, database/role creation, replication and RLS bypass. A read-only
  integration probe rejected the current elevated **development** role with
  `config.E002`; no roles or grants were changed. This flag check does not audit
  all grants or role memberships. Provision runtime, migration and backup
  credentials separately; isolated restore verification requires `CREATEDB`.
- A full-suite attempt exposed an outdated forecast-cache test mock after the
  earlier report date-range change. Its signature now accepts the optional
  dates; the period/cache-reuse/forced-refresh assertions are retained.
- The handoff now requests historical recorded-line labels, mutation-driven
  client refresh behavior that preserves backend throttling, explicit forecast
  evidence limits, production API configuration and dependency triage. A new
  read-only npm advisory audit found 10 affected packages (9 high, 1 moderate),
  superseding the pasted report's three-package snapshot without establishing
  browser exploitability or changing frontend dependencies.

Focused verification: 17 backup/deployment regressions and all 105 configuration
tests pass in disposable PostgreSQL databases with isolated caches and a fast
test password hasher. The 17 include the Unicode restore and POSIX permission
tests. Normal system checks, migration consistency, Python syntax (348 files)
and whitespace checks pass. A production-mode configuration-only check with
generated test credentials has zero errors and two HSTS policy warnings
(`security.W005`, `security.W021`); it does not verify running services.

The initial full run attempted **722 tests in 595 seconds: 721 passed and one
errored**, solely because of the outdated forecast-cache mock described above.
After correcting it, all 105 configuration tests passed. One additional browser-
origin configuration test was added while the full run was in progress and
passed in both focused runs; the current suite contains 723 tests. The complete
suite was not repeated after the mock-only correction. Logs:
`/tmp/rosario-deployment-tests.log`,
`/tmp/rosario-deployment-config-tests.log` and
`/tmp/rosario-deployment-focused-tests.log`.
A final database catalog check found no remaining task test databases or
isolated backup-verification databases.

Actual hosting/HTTPS/proxy behavior, runtime credentials/grants, Redis/SMTP
operations, Windows ACLs, current forecast evidence, dependency remediation and
complete staging browser workflows still require deployment verification.
Production readiness is not established by these source changes. No live
business data, database roles, hosting configuration or forecast fits were
modified by this follow-up.

## Backend fix follow-up

Backend changes now address the findings below; no frontend files were edited.

- Batch administration and ingredient catalog writes require an admin; permitted
  staff adjustments/counts and ingredient deactivation remain available.
  Received batch history cannot be deleted.
- Expired stock can be written off through an audited adjustment. Repeating a
  completed write-off is rejected without another movement. Expired counts and
  notifications share the same definition.
- Future receipts are rejected at the API boundary. Legacy future receipts are
  excluded from sellable totals, FEFO, and the physical ledger until received.
- Optional UUID body/header keys provide durable checkout/order replay protection
  with payload-conflict detection and concurrency locking. **IDs remain optional
  by the user's explicit choice; unkeyed submissions cannot be deduplicated.**
- Admin cancellation covers transactions with or without orders, records actor,
  time and reason, and preserves actual stock-return events. Concurrent order
  and transaction cancellation cannot restore stock twice.
- Current-day actuals update without clearing unchanged saved predictions.
  GET uses saved values and never fits; changes to fitting history still require
  refresh. An offline refresh is needed once for the revised graph identity.
- Account deactivation advances the token version, so reactivation cannot revive
  old sessions.
- `DJANGO_TIME_ZONE` is the common API/worker calendar. Conflicting settings API
  timezone edits are rejected, and legacy database values cannot override it.
- Runtime-settings cache invalidation and population occur after commit;
  rolled-back values are never published to the shared cache.
- Additive `recorded_cashier` distinguishes unknown original cashiers on imported
  or manually encoded invoices from the preserved `handled_by` import/entry owner.
  Existing frontend display consumers have not been changed.

Migration: `sales.0017_transaction_void_reason_transaction_voided_at_and_more`.
Apply migrations before serving this code. See README.md for retry keys,
cancellation routes, and the business-calendar contract. Existing historical
cancellations retain unknown actor/time; the migration does not invent them.

Verification after fixes: **703 backend tests passed**, including 22 new audit
regressions and concurrency checks, after crash recovery. The suite used
PostgreSQL, an isolated test cache and a fast test password hasher; its test
database was removed after completion. Django system checks, migration
consistency (`makemigrations --check --dry-run`) and whitespace checks passed.
Final suite log: `/tmp/rosario-backend-fix-full-final.log`.

Application database setup completed on 2026-10-27 (Asia/Manila): migration
`sales.0017_transaction_void_reason_transaction_voided_at_and_more` applied;
`migrate --check` confirmed no pending migrations. Forecast refresh job 15
succeeded across all maintenance stages, reusing unchanged fitted models. Real
weekly/monthly/yearly graphs were verified ready for 2026-10-27, with all three
views and 7/7/5 calendar points respectively (graph IDs 43/44/45). Refresh log:
`/tmp/rosario-forecast-refresh-20261027.log`. Hosting/deployment changes have not
been applied; the frontend remains unchanged.

## Findings from the supplied audit

1. **High — Staff batch writes remain enabled; ingredient cost writes widen the gap.**
   Both [product batches](inventory/views/productbatch_views.py#L10) and
   [ingredient batches](inventory/views/ingredientbatch_views.py#L12) expose a
   `ModelViewSet` with `IsAdmin | IsStaff` for every operation. A staff request
   successfully created 123 units, changed the batch expiry, and permanently
   deleted the unlinked batch. These operations do not create stock-adjustment
   records. Product batch cost has an admin check, but the
   [ingredient batch serializer](inventory/serializers/ingredientbatch_serializer.py#L26)
   has no equivalent: staff successfully changed an ingredient batch cost from
   10 to 999. Ingredient catalog writes also allow staff, including `unit_price`.

   If the stated policy is read-only staff inventory, enforce it at the API.
   Explicitly distinguish batch/catalog administration from permitted operational
   outflows: existing tests intentionally allow some staff stock adjustments and
   ingredient deactivation. Independently of role policy, deleting stocked
   batches should preserve a stock movement/audit trail rather than silently
   erase their inventory history. Hiding frontend buttons does not address this.

2. **Medium — Expired-status batches cannot be disposed.**
   [Adjustment validation](inventory/services/adjustment_service.py#L22) rejects
   both `expired` and `disposed` before considering the adjustment type. A
   full-quantity `expired` adjustment against a batch marked `expired` returns
   400 and leaves its stock untouched. Important qualification: a batch whose
   expiration date is past but whose status is still `available` can be written
   off successfully; the probe verified that control case too. The current code
   does not automatically convert every past-date batch to `expired`.

   Add a locked, audited disposal transition for expired stock, including a
   repeat-request policy. Continue rejecting consumption and positive stock
   restoration for expired/disposed batches.

3. **Cashier attribution — the supplied finding is primarily a display issue,
   with an ambiguous backend contract.**
   The [historical loader](sales/importers/load_annual_sales.py#L106) sets
   `handled_by` to the import owner; the
   [transaction serializer](sales/serializers/transaction_serializer.py#L58)
   exposes that account directly. It cannot establish who handled the original
   paper sale. `source_reference` lets clients identify imported records, and
   the supplied frontend audit already identifies the inconsistent consumer.
   Do not erase the import owner or fabricate an original cashier. A useful API
   improvement is an explicit nullable recorded-cashier field/provenance flag,
   while retaining the account responsible for importing/encoding the record.
   This review did not establish an additional cashier-data corruption bug.

The production frontend API address, overlay accessibility, layout, unused
frontend dependencies, and frontend cache invalidation are outside this backend
review. The localhost production-bundle claim was not revalidated here.

## Additional backend issues

4. **High — Direct checkout and order creation have no retry protection.**
   [Checkout](sales/views/checkout_views.py#L19) always creates a new sale;
   [order creation](sales/views/order_views.py#L96) likewise starts fresh. The
   UUID replay protection in
   [manual encoding](sales/services/encoding_service.py#L25) is not shared by
   those endpoints. Reposting an identical checkout body, even with the same
   `request_id`, returned two different receipts with HTTP 201 twice and reduced
   stock from 10 to 8. The unknown `request_id` field is ignored there.

   A timeout followed by a retry can therefore duplicate revenue and stock
   deduction. Extend the existing durable replay/fingerprint behavior to every
   public sale-creation route, or retire routes no supported client needs. The
   current manual-encoding UUID workflow already has replay protection; this
   finding does not claim it is broken.

5. **Medium — Today's new sale hides saved forecasts despite unchanged fitting inputs.**
   [Graph identity](forecasting/beta_graph.py#L107) includes the live displayed
   ledger revision. [Retrieval](forecasting/beta_graph.py#L281) requires an exact
   matching saved run and otherwise builds an unavailable-prediction response.
   The probe saved a ready graph, recorded a same-day sale, and verified that
   every training-input signature was unchanged. Nevertheless the response
   became `refresh_required` and its current/future estimates became null.
   Previously issued predictions may remain available through their separate
   replay path; this does not mean every historical point disappears.

   Midnight refresh consequently does not keep the dashboard's forecast lines
   available throughout a normal selling day. Separate prediction identity from
   live actual totals, or reuse saved fits for matching training signatures while
   recomputing actuals/evaluation without optimization. Keep GET free of training.
   The numerical optimizer was replaced with deterministic estimates in this
   probe; actual database persistence, signatures and retrieval were exercised.

6. **Medium — Old sessions revive after account reactivation.**
   [Account updates](accounts/services/admin_service.py#L93) and
   [deactivation](accounts/services/admin_service.py#L104) change `is_active`
   without advancing `token_version`. JWT authentication correctly rejects the
   account while inactive. However, the same access token works after
   reactivation, and the old refresh token can issue another access token.
   The focused probe verified this through the account and refresh APIs.

   If deactivation is intended to end existing sessions, advance the session
   version atomically when disabling an account through either update path.
   Reactivation would then require a fresh login. Password changes/logout already
   use the session-version mechanism.

7. **Medium — Future receipts are immediately sellable.**
   Batch serializers validate expiry relative to receipt date but accept a
   future `date_received`. [FEFO](inventory/services/fefo_service.py#L13) filters
   availability and expiry, not receipt date. The stock-total queries use the
   same incomplete eligibility rule. A batch created through the API with its
   receipt date two days ahead was successfully sold today.

   Either reject future receipt dates for physical inventory, or consistently
   exclude pending receipts from FEFO, stock totals and reports until received.
   Apply the rule to both product and ingredient batches.

8. **Medium — Sales without an order cannot be voided through the API.**
   All [direct checkouts](sales/services/checkout_service.py#L12), including those
   with an optional customer, create transactions without an order. Manual
   encoding's [walk-in branch](sales/services/encoding_service.py#L63) does the
   same. Cancellation exists only on
   [orders](sales/views/order_views.py#L138), while
   [transactions](sales/views/transaction_views.py#L39) expose read-only history.
   The probe verified a walk-in checkout had no order and that transaction
   deletion/update returned 405; there is no transaction void action.

   Mistaken walk-in entries cannot be corrected through the supported API and
   continue affecting stock, revenue and forecasts. Add an admin-only
   transaction cancellation workflow using the existing locked stock-return
   logic, preserving return events and guarding repeat cancellation. Keep
   imported historical records under their explicit correction policy.

9. **Medium — Expired inventory is undercounted when its status is `expired`.**
   [Inventory reporting](reporting/services.py#L416) restricts
   `expired_batch_count` to `status='available'`. A past-date batch explicitly
   marked `expired`, with 10 units physically remaining, produced a count of
   zero. Its ledger row can still mention expired stock, so different report
   sections disagree. Both product and ingredient counts use this rule.

   Count expired stock awaiting disposal using one shared definition that covers
   date-expired available stock and explicitly expired stock with remaining
   quantity, while excluding completed disposals.

10. **Medium, conditional on configuration — Web and worker timezones diverge.**
    [HTTP middleware](systemsetting/middleware.py#L18) activates the timezone
    stored in `SystemSettings`. Management commands use Django's environment
    timezone; the scheduler explicitly follows Asia/Manila. The
    [source revision](forecasting/data.py#L103) includes the active timezone.
    A graph saved under Asia/Manila became `refresh_required` under Asia/Tokyo
    even with the same date and unchanged ledger. Thus changing the supported
    database timezone setting can make every web request reject worker results;
    a manual refresh launches another management process with the same mismatch.

    Establish one business timezone for HTTP, commands and scheduling, or prevent
    unsupported timezone changes. Default Manila configuration avoids this
    particular mismatch.

11. **Low, transactional saves — Runtime cache invalidation runs before commit.**
    The [settings signals](systemsetting/apps.py#L12) call
    [cache deletion](systemsetting/runtime.py#L62) immediately. During a settings
    save inside `transaction.atomic()`, another database session can reload and
    cache the old committed row. After commit, the database contains the new
    value but the cache serves the old one for up to 300 seconds. A two-session
    probe reproduced this. The ordinary settings endpoint is not currently
    wrapped in a request transaction, so this is a conditional concurrency flaw,
    not a claim that every settings PATCH is stale.

    Schedule invalidation with `transaction.on_commit`, as the general business
    data cache already does. Separately, TODO.md's broader failed-invalidation
    staleness item remains an operational risk; the existing staff visibility
    fail-closed regression tests passed.

## Initial audit verification and remaining limits

- `venv/bin/python manage.py check`: passed.
- Existing accounts, inventory, sales, settings, reporting and config tests:
  **527 passed** against PostgreSQL, using the project's normal settings.
- **11 focused audit probes passed**, reproducing the behaviors described above
  plus the successful available-status disposal control case. Assertions in
  these probes describe the bugs as observed; passing them does not mean the
  bugs are fixed. Temporary test databases were used and removed by the runner.
- Existing forecasting regression suite: **154 passed** against PostgreSQL.
  Together with the other applications, **681 existing backend tests passed**.
- Temporary reproductions: `/tmp/rosario_backend_audit_probe.py`.
  Logs: `/tmp/rosario-backend-audit-tests.log`,
  `/tmp/rosario-backend-audit-probes.log`,
  `/tmp/rosario-backend-audit-forecast-tests.log`.
  The temporary probe/forecast runner uses an isolated local-memory cache and a
  fast test password hasher. Its graph identity probes mock optimization only.
- At the time of the initial audit, no production database writes, deployment,
  external messages or application fixes were performed; this report was its
  only repository addition. Backend fixes are described in the follow-up above.
- Hosting supervision, actual Redis/SMTP behavior, reverse-proxy configuration,
  staging journeys and production data cleanliness were not validated. The
  scheduler provisioning and real-only deployment database tasks in TODO.md are
  still deployment prerequisites, not newly reproduced code defects.

Recommended order: settle/enforce staff batch and cost permissions; protect every
sale-creation route against retries; make cancellation cover transactions without
orders; fix receipt eligibility and expiry disposal/reporting; address graph
identity and session invalidation; then align timezones and cache commit timing.
