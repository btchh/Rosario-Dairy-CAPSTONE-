# Backend audit — 2026-10-26

Reviewed backend commit `ebd2521b`, the two supplied screenshots, and the later
pasted frontend audit. Application code was not changed during that initial
review. The original findings below describe the pre-fix behavior and distinguish observed behavior from policy decisions and
deployment work. Severity reflects potential impact, not proof of exploitation.

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
