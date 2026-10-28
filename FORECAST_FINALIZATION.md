# Forecast backend finalization — 2026-10-28

The user retained SARIMA after the Chapter 4 comparison. This pass connects that
choice to eligible regular-sales forecasts and exposes their saved seasonal
paths. The comparison results remain unchanged. Retaining SARIMA is a model-family
decision; it does not mean weekly SARIMA outperformed its baseline.

## Implemented behavior

| Series | Backend behavior |
| --- | --- |
| Monthly regular | Reuses the exact-source primary seasonal SARIMA artifact; eligible missing fits await maintenance instead of changing to a nonseasonal model |
| Weekly regular | Reuses the exact-source weekly SARIMA artifact; its configuration is selected on earlier validation, never later test outcomes |
| Short regular history | Explicit provisional fallback when the required training/validation/test history is unavailable |
| Feeding | Retains its own sparse-history model and limitations; no missing invoices are converted to zero demand |
| Combined | Sums available regular and feeding predictions; remains unavailable when either component is unavailable |

Weekly selection requires 130 consecutive complete source weeks: at least 104
training weeks, 13 validation weeks and 13 test weeks. Partial source-month
boundary weeks are excluded consistently with the existing weekly research.
The original data provide 156 usable weeks. Saved coefficients, source revision,
training signature, chronology, parameter schema and earlier selection evidence
are checked before reuse. An invalid eligible saved fit is withheld.

Graph engine `centered-beta-sarima-v6` and provisional engine
`actual-provisional-sarima-v4` share the same weekly regular artifact. Existing
endpoints, permissions, field types, centered windows, thresholds and database
schema are preserved. No frontend source is edited.

## Full seasonal visibility

The graph adds `seasonal_outlook` containing 52 weekly or 12 monthly points from
the current bucket onward. The existing centered seven-point windows remain
available. Yearly returns `seasonal_outlook=null`; its current-year actuals-plus-
estimates calculation stays distinct from a forecast issued before the year.

The longer path uses the same saved component origins as the current/future
centered points. Its first four predictions agree with those existing points.
It is saved during offline refresh and replayed during GET; GET never fits or
fills a missing outlook. Invalid paths and inconsistent Combined sums are
withheld. Current recorded actuals can update without altering saved forecasts.

The longer display does not create additional issued-prediction evidence.
Existing issued rows are preserved and remain separately identified. Only the
existing centered issuance policy creates new future issue records.

Frontend adoption is described in [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md).
The current frontend continues displaying the centered response until it adopts
the longer path. Seasonal curves are model output; no peaks, smoothing or
interpolated amounts are manufactured.

## Freshness and evidence

`model_details.projection_steps` counts from the final fitted bucket. The legacy
`horizon` remains relative to the extraction cutoff. Annual components describe
their monthly projection-step span. Combined keeps separate component origins.

Top-level and per-view `readiness.source_freshness` identifies `current`,
`stale_data` or `unavailable` source observations. Legacy `status=ready` describes
saved availability; it does not grant production approval. Model labels identify
seasonal SARIMA versus local-level/nonseasonal fallbacks. Primary backtests are
explicitly labelled rolling one-week or one-month evidence, with baseline
comparisons; they do not establish 52-step accuracy.

Original sales still end in December 2025. No generated 2026 receipts were
restored. The current regular weekly target is 44 steps beyond its final fitted
week; feeding is 46 steps beyond its own final week. Refreshing cannot supply
the missing observations. Strict monthly `stale_data` remains expected, and
unknown completed 2026 months continue to withhold the current annual nowcast.

The Chapter 4 comparison and assumption checks are retained in the private
[comparison report](forecast_experiments/chapter4-comparison-20261028/CHAPTER4_FORECAST_COMPARISON.html)
and [assumptions review](forecast_experiments/chapter4-comparison-20261028/assumptions-review-verified/SARIMA_ASSUMPTIONS_REVIEW.md).
Their source, chronology and statistical limitations still apply. Actual
forecast quality and publication approval require reviewed coverage and later
observations at the intended horizon; neither is fabricated by this pass.

Read-only saved-parameter residual checks are also retained privately. Monthly
regular Ljung–Box p-values are 0.3660 at lag 6 and 0.7294 at lag 12. Weekly
regular values are 0.0057 at lag 13, 0.1102 at lag 26 and 0.5294 at lag 52.
The lag-13 result indicates remaining short-lag structure under this diagnostic;
the weekly residuals are not declared white noise. These are descriptive
in-sample checks, with one diffuse monthly observation excluded and model degrees
of freedom specified; they neither reselect the model nor approve long-range
accuracy. The procedure follows the
[statsmodels Ljung–Box documentation](https://www.statsmodels.org/v0.14.6/generated/statsmodels.stats.diagnostic.acorr_ljungbox.html).

## Verification status

The resumed focused suite passed 48 tests. The first full run passed 305 of 306
tests and exposed an unnecessary short-history fallback refit at a new monthly
boundary. The component identity now excludes unused strict-weekly dependencies
for that fallback. All 52 affected tests passed after the fix. The final full
forecasting, reporting and dummy-cleanup suite passed **306/306 tests** in
473.137 seconds against a new disposable PostgreSQL database and an isolated
local-memory cache. The disposable database was removed successfully.
Django system checks, Python syntax checks,
`makemigrations --check --dry-run`, `migrate --check --noinput` and whitespace
checks passed. No migrations were applied.

Tracked local maintenance job 24 succeeded and reused weekly/monthly primary
model 4, provisional run 8 and graph runs 55/56/57. Before/after hashes confirmed
every retained row across 46 models unchanged. The refresh job was the only new
forecast record in this verification interval; concurrent authentication-token
activity is recorded separately. The source revision remains
`bfe1185e081c1fa066c6c45f121a97a41e35b4a6d8d9d71c5387dbd45fc37875`.

Saved-state serving checks passed with optimization, graph fitting and outlook
regeneration forbidden. Eleven direct/proxied HTTP, status and role checks
passed; the weekly legacy chart also exposes provisional v4's matching SARIMA
artifact. Staff returns 403 and anonymous requests return 401.

| Saved outlook | Regular revenue range | Feeding behavior |
| --- | --- | --- |
| 52 weeks, October 26, 2026–October 24, 2027 | PHP 289,102.57–308,780.42; 52 distinct estimates | PHP 163,932.00 weekly local level |
| 12 months, October 2026–September 2027 | PHP 1,173,471.09–1,469,839.03; 12 distinct estimates | PHP 417,088.00 monthly local level |

These modest curves are the saved model output. No target-horizon accuracy or
production approval is inferred from the number of distinct predictions.

Private test logs and before/after row fingerprints are retained in
`forecast_experiments/forecast-finalization-20261028-9d3e1655/`.
