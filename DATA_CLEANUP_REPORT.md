# Generated-data removal — 2026-10-28

The generated January 1–November 7, 2026 dataset has been removed from the local
development database, together with the saved studies/fits and graph issues
proven to derive from it. The original source records are restored as the sole
sales input. No replacement sales or fabricated outcomes were inserted.

## Exact removal and preservation

| Removed records | Count |
| --- | ---: |
| Transactions | 667 |
| Transaction items | 1,289 |
| Orders | 667 |
| Order items | 1,289 |
| Customers created only for the generator | 2 |
| Pattern study / monthly fit / weekly fit / provisional fit | 1 each |
| Graph runs | 3 |
| Graph issues attached to those runs | 24 |

Removed generated revenue totals **PHP 54,406,801.72**. There are **4,835
remaining original transactions**, 324 customers, and no transactions dated
2026. The latest original sales date is **December 31, 2025**. Legacy reserved
`2026:NNNNNN` dummy receipts, explicit demo runs and synthetic experiments were
also checked: none remained before or after this cleanup.

Selection used exact IDs/references from the private insertion manifest, verified
against its hashed generation plan, amounts, dates, segments and dependencies.
No broad date/name-based deletion was used. Historical stock batches were
retained. The two removed customers had no unrelated references. There were no
stock returns, live-stock movements or checkout submissions attached to these
receipts.

The operation created a new full-precision private backup of **63,983 model
records**, restored it into a disposable PostgreSQL database, verified every
restored fingerprint, rehearsed the exact cleanup and removed that database.
Only then was cleanup applied atomically to the local database with table locks.
Every current protected model row was fingerprint-checked before commit, with
rollback required on any difference. Original sales/customer/account/inventory
and settings hashes also match the insertion-time baseline exactly.

The resulting forecast-input fingerprint exactly equals the fingerprint from
before generated sales were inserted. Existing genuine-data fits and issued
forecasts, current authentication/blacklist state, inventory and unrelated
business records were preserved. Historical maintenance job logs remain audit
history; their old synthetic-result JSON is not current model evidence.

Recovery files and the reproducible exact-ID operation are private and gitignored
under `backups/dummy-removal-20261028-c9c9de72/` (directory 0700; backup, manifests
and scripts 0600). The insertion manifest remains private provenance/recovery
history, not active sales data. No historical backup was restored over the
application database.

## Refresh and readiness

Tracked maintenance **job 22 succeeded** after deletion. It reused the original
monthly model 4, weekly model 4 and provisional model 6; it created the corrected
graph v5 runs 52–54 and pattern study 13. It created no prospective scores,
coverage reviews, monthly issued forecasts or promotions.

The monthly model now correctly reports `stale_data`: original observations end
in December 2025. The original current year has no completed monthly actuals, so
the annual actuals-plus-remaining-estimates nowcast is unavailable rather than
silently inventing January–September revenue. Saved graph availability remains
separate from production approval. Long-range research projections may still be
calculated from the older history; they are not a fresh next-period validation.

## Weekly seasonality and the current week

Weekly forecasting is feasible to investigate for regular sales: there are
**156 consecutive complete weeks**, January 2, 2023–December 22, 2025. The backend
already evaluates weekly SARIMA with season length 52 and a same-week-last-year
baseline. Monthly aggregation is not mandatory.

Original feeding contributes **30 invoice-bearing weeks across 55 calendar
weeks**, with 25 weeks whose absence of invoices is not verified zero demand.
Only three feeding week pairs are observed 52 weeks apart. Earlier regular-only
years must not be described as verified historical feeding demand.

For the current week, **October 26–November 1, 2026**, the 52-week seasonal
comparison is **October 27–November 2, 2025**:

| Recorded matching-week revenue | Amount |
| --- | ---: |
| Regular | PHP 245,159 |
| Feeding | PHP 412,699 |
| Combined | **PHP 657,858** |

Repeating that amount is a transparent weekly seasonal baseline. It is neither
a measured 2026 outcome nor a daily forecast for October 28. The current week
is 44 steps after the latest complete regular training week. For a daily
same-weekday comparison, October 28, 2026 maps back 52 weeks to October 29,
2025: regular revenue is PHP 6,190 and no feeding invoice is recorded; unknown
feeding demand cannot be asserted to be zero.

On the same 13 original regular holdout weeks (September 29–December 22, 2025),
trailing-four-week mean WAPE is **20.2992%**, same-week-last-year **25.9772%**,
saved weekly SARIMA **26.8135%**, and previous-week **32.0101%**. These are
retrospective research scores, not prospective 2026 accuracy. The seasonal
comparison is useful, but it does not automatically outperform other methods.

A detached joint-versus-component experiment also tests the user's proposal,
using a shared November 10, 2025 origin and a six-step November 17–December 22
path. Model winners were fixed using the preceding six-week validation window;
no SQL or application writes occurred during fitting. Direct Combined selected
AR(1); both separate components selected local level. Test WAPE was 153.8629%
for direct Combined, 425.1271% for the component sum, and 120.6383% for the
52-week comparison. The earlier-selected baseline was trailing-four-week mean
(validation 65.7682%, versus seasonal 85.9740%); it scored 286.5074% on this
later path. Selecting seasonal after seeing that test would be hindsight.

This experiment forecasts **recorded classified invoice revenue**, not certified
consumption/demand; its earlier regular-only totals do not establish feeding
coverage. All six targets have regular receipts, while only four have feeding
receipts; those unknown feeding periods remain explicit. The large errors
reflect a feeding billing-regime change, not a proven usable seasonal Combined
model. This six-step test differs from the strict regular model's rolling
one-step, thirteen-week task and must not be compared as the same accuracy
measure. Historical data have already been inspected; this is not untouched
acceptance evidence. No algorithm or threshold was changed on these results.

Private aggregate results and reproduction code are retained under
`forecast_experiments/pattern-review-20261028-640f6449/original-weekly-comparison/`.
The next model decision should compare defined targets/horizons and certified
source coverage; it must not treat newly generated outcomes as proof.

## Verification

### Why the weekly lines were flat after cleanup

This section records the graph v5 snapshot immediately after cleanup. The later
user-selected weekly SARIMA routing, full seasonal outlook and current saved
values are documented in [FORECAST_FINALIZATION.md](FORECAST_FINALIZATION.md).

A subsequent read-only check of graph run 52 confirms its regular component is
nonseasonal AR(1), its feeding component is local level, and Combined is their
sum. The chart's generic “SARIMA estimates” subtitle does not describe these
selected model specifications accurately. The separate strict weekly SARIMA fit
is not used by the weekly graph; the graph reuses the primary model only for
regular monthly inputs (`forecasting/beta_graph.py`, `_primary` selection).

Current regular weekly predictions are PHP 286,841.19, feeding PHP 163,932.00,
and Combined PHP 450,773.19 for each of Oct 26, Nov 2, Nov 9 and Nov 16, 2026.
The AR(1) coefficient is .5020. These targets start 43 model steps after its
last training bucket (Dec 29, 2025); the transient has decayed below currency
precision. Feeding local level carries a constant expected amount. The earlier
regular validation selected AR(1) at 23.2307% WAPE over seasonal AR at 24.2638%;
that six-period selection does not validate a 43-step path.

Filtering the separately saved weekly SARIMA state, without fitting or changing
the served model, gives regular predictions of PHP 294,116.07, 296,001.83,
289,102.57 and 308,780.42 for those same four targets. Its seasonal coefficient
is approximately .1125, so even this fit produces modest seasonal variation,
not a full replay of the previous year's spikes. These are model-path comparisons,
not current-outcome accuracy scores; the two saved fits have different final
training buckets and the comparison is not a matched-origin acceptance test.

The monthly regular path does vary: PHP 1,173,471.09 (October), 1,246,036.80
(November), 1,309,867.18 (December) and 1,366,717.03 (January). Weekly/monthly
graph windows expose only current plus three future targets, while annual sums
remove within-year fluctuations. The frontend copies numeric predictions without
averaging them; monotone interpolation changes the connecting stroke only.
Read-only centered/boundary frontend tests passed 30/30; frontend source stayed
clean. Model identification, longer seasonal visibility and matching-horizon
evaluation remain separate from inventing peaks or forcing a model switch.

The complete forecasting and dummy-cleanup suite passed **199/199 tests** in
406.374 seconds in a fresh disposable PostgreSQL database with an isolated cache.
The backup restore/cleanup rehearsal separately restored all 63,983 records with
exact full-precision fingerprints before deleting the disposable database.
All current test/rehearsal databases were verified removed.

Django system checks, Python syntax, migration consistency/status and whitespace
checks passed. No migrations were applied. Saved-read verification prohibits model
optimization; all graph components preserve Combined = Regular + Feeding when
available, and the missing-actual current annual nowcast remains null.

Ten HTTP checks passed: all three graph periods directly and through the frontend
`/backend` proxy, original-only model/pattern status, Staff 403 and anonymous 401.
The backend runs at `http://127.0.0.1:8000`; the existing frontend runs at
`http://127.0.0.1:5173`. No frontend source was edited.

Final verification confirms unchanged retained-row fingerprints across **46
models**, absent generated IDs and contaminated fits, exact restored original
sales-input revision, 4,835 remaining receipts and 324 customers. The full proof
is private `backups/dummy-removal-20261028-c9c9de72/verification.json`.

No database models, migrations, permissions, forecasting algorithms, frontend
source or local development settings were changed by the cleanup. The preceding
origin/pattern repairs remain in the backend working tree. No push was performed.
