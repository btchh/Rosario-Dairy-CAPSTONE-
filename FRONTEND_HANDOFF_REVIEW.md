# Frontend handoff review — 30 October 2026

**Historical review, not an active request list.** Frontend `f6e4546a` is now
deployed locally and passed 614 tests, TypeScript and admin/staff browser smoke
checks. Its current open requests and API details are in
[FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md). The older missing-feature claims and
dependency counts below are not a fresh audit. Removed handoff sections remain
in the [versioned handoff](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/7a75a0ab/FRONTEND_HANDOFF.md).

**Result: partially satisfied.** Reviewed frontend `main` at `49c3cb42`
against `FRONTEND_HANDOFF.md`, with the Docker handoff checked separately.
Most requested display work is present. The remaining items below prevent
marking the entire handoff complete.

**Client wording revision later on 2026-10-30:** the user requested a simpler
client interface. Item 3's earlier request to show strict monthly evidence in
the normal UI is superseded. The new outstanding work is removing technical
evidence panels/issued-comparison controls from that view and shortening forecast
report messages. See the authoritative **Client wording revision** section in
`FRONTEND_HANDOFF.md`. Technical API fields and validation rules remain intact.

**Further client feedback:** change the gray joining segment to orange, matching
the past-prediction line up to the current point. Remove the verbose forecast
and report date-help paragraphs. These are pending frontend requests, described
in the handoff's **Follow-up: orange connector and concise date controls**.

This is a source/contract, component-test, type-check and build review. It does
not establish production capacity or replace browser/end-to-end staging tests.
No frontend tracked files were changed, and no business database writes ran.

**Subsequent backend scope change:** ingredient/supplier management is now
retired by `inventory.0012_product_only_inventory`. This review's frontend
checkout still has legacy ingredient types, list requests and labels. Their
removal is pending, as detailed in the handoff's **Product-only inventory
cleanup** section. Temporary empty legacy GET responses preserve older clients'
parallel product-inventory and notification loads; legacy writes return 410.
This is an additional request beyond the original `49c3cb42` feature review.

## Remaining requests

### 1. Checkout and order retry UUIDs: not implemented

`src/features/pos/api/checkout.service.ts:6` constructs the checkout body without
`request_id`; `src/features/orders/api/orders.service.ts:30` does the same for
orders. Neither sends `Idempotency-Key`. The existing guard prevents blind
retries and must remain until UUID-based recovery is implemented.

`src/lib/saleSubmission.ts:38` still incorrectly says the routes have no
idempotency support. Encoding has its own request identity, but that does not
complete the separate checkout/order requirement.

Required: persist one UUID with the exact logical payload, reuse it for an
ambiguous matching retry, use a new UUID for a new sale, and cover 201/200 replay,
409 changed-payload conflicts, concurrent submissions and reload recovery.
Verify stock is deducted once with backend integration tests.

### 2. An open customer profile retains stale totals: reproduced

`src/features/customers/pages/AdminCustomers.tsx:77` stores the selected customer
as an object separate from the current query data. `openView` captures that
object, and the profile reads `selected.totalSpent` directly. Cache refreshes
update the table/mobile records without replacing this selected object.

Reproduction using the upstream component harness, with no API writes:

1. Seed a customer with `totalSpent: "100.00"` in the customer list cache.
2. Render Customers, subscribe its query effect, and open that customer's profile.
3. Update the customer cache to `totalSpent: "200.00"` and render again.
4. The table has `200.00`; the open profile still displays `₱100.00`.

A temporary, in-memory extension of the existing test harness reproduced this
failure with Node web storage disabled, independently of the seven known POS
test failures. The extension and log were kept under `/tmp`, outside the
frontend repository. It adds no permanent frontend test or source modification.

Required: derive the profile record from its selected ID and the latest list,
or explicitly refresh the selected detail. Add coverage for invalidation after
a sale/cancellation while the profile is open. Do not recalculate lifetime
totals from receipt pages.

### 3. Strict monthly approval details: earlier client-display request superseded

Graph readiness, research-only publication, source age, model evidence and
baseline comparisons are displayed in `ForecastMetadata.tsx`. This display is
implemented, but the later wording revision requests removing technical details
from the ordinary client view. Retain the underlying evidence internally.

However, `primary_monthly_status_url` appears only in the response type
(`src/features/dashboard/api/forecasting.service.ts:79`). There is no frontend
request to `/api/forecasting/models/` and no UI displaying that endpoint's
strict monthly approval and missing-evidence reasons. `getRegularStatus` uses
the separate `/api/forecasting/forecast/` contract and has no active UI caller.

The earlier recommendation was to fetch and present strict monthly evidence.
The user's later wording revision withdraws that requirement for the ordinary
client. Retain this endpoint for internal verification and documentation; show
short, understandable availability/planning notices in the normal interface.
Never infer production approval from a graph's legacy `ready`.

### 4. Node 26 test-storage isolation: still not fixed

`tests/frontend-ui.test.cjs:133` resets local storage and caches but does not
reset/mock session storage. An earlier ambiguous sale/order result remains in
native session storage and blocks later POS tests.

- Default `npm test`: **419 passed, 7 failed** under Node **26.12.1**.
- `node --no-experimental-webstorage --test tests/*.test.cjs`: **426 passed**.

These results reproduce the already documented isolation issue. The flag is
a verification workaround, not completion of the requested test setup fix.
Reset/mock session storage and pending-sale state per test; preserve the real
production duplicate-sale guard.

### 5. Dependency review: still outstanding

A fresh read-only `npm audit --json` reports **10 affected packages: 9 high,
1 moderate**, with fixes advertised by the registry:

`baseline-browser-mapping`, `browserslist`, `follow-redirects`, `nanoid`,
`postcss`, `react-router`, `react-router-dom`, `source-map-js`, `tar`, `vite`.

These are advisory classifications, not a finding that all ten are exploitable
in the deployed browser application. Assess application/build/dev-server paths,
update manifest and lockfile together or record justified scoped exceptions,
then repeat the audit, tests, strict type check and build. No dependency upgrade
was made during this review.

### 6. Production origin and staging verification: still outstanding

The API client supports `VITE_API_URL` / `VITE_API_BASE_URL` and otherwise uses
the relative `/backend` path. The source alone cannot establish that a deployed
build uses the provisioned HTTPS backend or a correctly configured proxy.

Verify both roles, login/session refresh, a complete sale/order/cancellation,
customer totals, previews/PDFs, refresh cooldowns and forecast availability
against the actual staging configuration. Multi-account refresh and stock-once
claims need integration verification; the component tests mock HTTP responses.

### 7. Orange connector and concise date controls: newly requested

Both quoted date explanations are hardcoded frontend paragraphs:

- `src/features/dashboard/components/admin/ForecastChart.tsx:154`,
  `forecast-date-help`, beginning “The To date anchors the server's centered
  window and seasonal outlook.”
- `src/features/reports/components/ReportDateRangeControl.tsx:57`,
  `report-date-help`, beginning “Dates use Asia/Manila time and apply to all
  reports.”

Remove these paragraphs and their now-unused accessibility references; retain
labels, validation, selected dates and reset. Remove similar persistent graph
implementation notes, keeping short availability messages and accurate dates.
No backend response change is needed for the quoted paragraphs.

The connector currently uses `C.muted` in `ForecastChart.tsx:191`. Change it to
`C.orange` with the past line's `5 4` dashes and existing width. Keep all endpoint
and adjacency guards, null gaps and values. Green still begins at the current
point. This supersedes the earlier neutral-color instruction, which the source
implemented correctly; the new visual request is not yet implemented.

## Implemented requests

| Request | Evidence / outcome |
| --- | --- |
| Customer totals | `customers.service.ts` maps the server decimal string; `totalPurchases.ts` formats without floating-point conversion. Table, profile and mobile card display Total Purchases and the pending/voided definition. Zero, missing and large values have mapping/UI coverage. Refresh exception: item 2 above. |
| Seasonal outlook | `ForecastChart.tsx` offers Centered window and Seasonal outlook. The full returned 52-week/12-month horizon is used for Regular, Feeding and Combined. Yearly returns to the annual centered view. |
| Latest outlook and original issues | Separate Latest outlook line and optional Original issued comparison, with origin/horizon/provenance in tooltips. Numeric zero survives; missing components remain unavailable. |
| Current period and boundary | `is_current`/`end_date` and joining logic are implemented, with null gaps preserved. The current gray styling and technical caption need the orange/concise revision in item 7. |
| Model metadata and freshness | Component diagnostics are implemented. The later client wording revision requests moving these details out of the ordinary client view; strict monthly evidence remains an internal API/documentation concern. |
| Plain-language client forecasting | Newly requested simplification is pending. Keep meaningful availability/estimate notices; remove technical panels, issued-comparison controls, verbose report diagnostics and the forecast/report date-help paragraphs from the normal client screen. |
| Annual nowcast | Calculation disclosure, completed/missing/invalid months, current-month actual floor and component breakdowns use server values. No client recomputation or monthly-to-annual accuracy claim. |
| Forecast dates and nulls | Historical `as_of` remains in requests/cache keys. Centered buckets overlap the selected range without proration; Seasonal outlook preserves the full saved horizon. Missing values stay unknown. |
| All six report date ranges | Shared validation supports inventory/forecast as well as the other four report types. Previews and PDFs use matching bounds and dated cache keys/filenames. Inventory health is separately timestamped; forecast range notes remain visible. |
| Reports and cache handling | Writes invalidate customer/sales/inventory/report groups. No automatic explicit report refresh after checkout/order writes. Manual refresh posts `{}` and accepts coalesced 200 responses. Preview/PDF Retry-After cooldowns are separate and stale content is retained. |
| Registration | Unicode code-point limits, Python-compatible username stripping before NFKC, unchanged password whitespace and boundary tests remain implemented. |
| Historical receipts | Imported line labels preserve recorded source amounts and do not allocate invoice discounts; ordinary local sales retain net-amount labels. |
| Input/error compatibility | Shared client preserves detail/error/field/non-field messages and Retry-After. Existing forms retain drafts on validation failures. No contrary change to the required backend input shapes was found. |

## Verification record

| Check | Result |
| --- | --- |
| Upstream commit / working tree | `49c3cb42`, clean and synchronized with `origin/main` |
| Production build | `npm run build` passed on this exact checkout during the pull; bundle-size and Node deprecation warnings remain |
| Strict TypeScript | `node_modules/.bin/tsc --noEmit` passed |
| Default Node tests | 419 passed, 7 known POS isolation failures |
| Storage-isolated verification | 426/426 passed; production storage guard unchanged |
| Additional open-profile freshness probe | Failed as described above; confirms a gap not covered by the existing 426 tests |
| Registry advisory audit | 9 high, 1 moderate; no fixes applied |
| Browser/end-to-end staging | Not performed; no production origin provisioned |

## Separate Docker handoff

`FRONTEND_DOCKER_HANDOFF.md` also describes an optional frontend image in the
same Compose stack. The frontend checkout contains no tracked Dockerfile or
frontend Nginx configuration, so that packaging request has not been completed
by this update. The existing backend image/package does not automatically gain
the newly pulled frontend code.

For a combined stack, the frontend owner still needs to build the Vite assets
with `/backend`, package them with an SPA fallback and backend proxy, and test
the combined Compose configuration. The proposed Cloudflare Pages frontend
deployment is a separate route and does not require that frontend container;
the backend/database can continue to run in Docker on OVHcloud.
