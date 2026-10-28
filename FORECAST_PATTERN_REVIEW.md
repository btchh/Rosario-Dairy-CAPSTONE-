# Forecast pattern and origin review — 2026-10-28

**Snapshot note:** the generated-data findings and numeric chart examples below
describe the pre-cleanup demonstration snapshot. The user subsequently requested
full removal; the 667 generated receipts and proven dependent forecast artifacts
have now been removed. See [DATA_CLEANUP_REPORT.md](DATA_CLEANUP_REPORT.md) for
the current original-only dataset, weekly evidence and refreshed readiness.

The most visible jump was caused by mixing forecasts issued at different times.
The flat tails have a separate explanation: selected nonseasonal models revert
toward a level, and the original feeding history is sparse. Neither appearance
alone establishes an algorithm defect or predictive accuracy.

The backend implements the requested **one latest outlook**, retaining genuine
issued forecasts separately. The code repairs themselves do not modify sales,
inventory, accounts, schema, model selection rules, quality thresholds or frontend
source. Separately authorized generated-data removal is recorded above. This is a
local demonstration system; these corrections do not establish production
forecast approval.

## What the recorded patterns show

The private insertion manifest identifies exactly the 667 generated 2026
transactions. This review uses those exact IDs to separate source evidence;
ordinary generated records were still included at this study's extraction time.
They have subsequently been removed. “Original” below means outside that manifest,
not independently certified against source documents. Analyses exclude the
unfinished current period and rows dated after the extraction boundary.

| Observation | Evidence | Consequence |
| --- | --- | --- |
| Generated regular history repeats the previous year | Completed Jan–Sep 2026 monthly totals equal the corresponding 2025 totals × 1.03 within rounding; matching-month correlation is 1.0000 | A strong annual pattern in this extension was constructed, not independently observed |
| Generated billing cadence differs from original history | Exactly two generated regular receipts each day; generated feeding invoices are all Fridays, while original feeding has no Friday invoices | Weekly curves and errors partly describe generator assumptions |
| Original regular annual shapes are weakly repeatable | Matching-month correlations: 2023/2024 = .3719; 2024/2025 = .0967 | A seasonal wave should not be forced merely because the chart is flat |
| Original regular revenue growth is not a volume measure | Revenue rose about 11% annually in 2024/2025; invoice counts fell about 5%; mean invoice value rose about 17–18% | Revenue alone cannot distinguish price, customer mix, billing consolidation and physical demand |
| Original feeding coverage is sparse | 38 invoices on 30 dates; 10 observed months in a 14-month span; February and May–July 2025 have no feeding invoice | Those missing months are unknown, not reviewed zero demand |
| Feeding billing dates are concentrated | 47.62% of original feeding revenue is dated Sunday; generated feeding is 100% Friday | Invoice date is not verified delivery or consumption cadence |
| Customer concentration matters | Top ten original regular customers account for 61.96% of revenue | A few customer/program billing events can dominate a period |

At the October 26 weekly training cutoff, generated records contribute 24.41%
of regular revenue and 50.27% of feeding revenue. Latest original records are
December 31, 2025 (regular) and December 14, 2025 (feeding).

Source review is still required for eight original invoices dated March/May
2022 whose source references begin with 2023, a September 28, 2023 regular
billing concentration (36.51% of that month's revenue), and 51 unclassified
rows totaling PHP 2.309 million. No dates, classifications or amounts were
corrected automatically.

## Why the future line becomes flat

A nonseasonal AR(1) forecast with long-run level `mu` follows
`E[y(t+h)] = mu + phi^h * (y(t) - mu)`. When `abs(phi) < 1`, the influence of the
last observation shrinks with each step. This is a property of the selected
model, not a missing curve. A local-level forecast can be flat from its first
step. Seasonal candidates require adequate earlier history and must win earlier
validation; a seasonal-looking chart is not a selection criterion.

The current provisional feeding monthly coefficient is approximately `.0157`.
Its tail therefore approaches PHP 3.63 million almost immediately. There are
19 observed months across 23 calendar months, below the history required for
the annual seasonal candidate's training plus validation/test windows. Current
weekly provisional regular/feeding coefficients are approximately `.6401` and
`.5558`; their paths approach their levels more gradually. These chart models
are not all the strict primary SARIMA model.

No coefficients, candidate definitions, selection windows, gates, seasonal
eligibility rules or future predictions were adjusted to create visual movement.

## Confirmed backend defects and repairs

### One current/future origin, with original issues separate

Before this change, the current weekly Combined point reused an older issued
PHP 450,773.19 forecast, while the later points used a newer model. That same
newer saved model projects PHP 1,158,163.64 for the current week. Consequently,
the apparent crash and recovery joined different information sets.

Current and future points now use the same latest closed-period model origin.
For this snapshot the weekly origin is October 26, and the monthly origin is
October 1. Completed historical points still prefer coherent original issues
where available; otherwise they remain explicitly reconstructed historical tests.
The boundary between a historical issue/test and a new outlook is still a change
of origin, so a visually smooth join is not guaranteed or manufactured.

Older pre-target values remain available through `issued_prediction` and
`issued_provenance`. Provenance identifies the actual issue row/run, engine
version, issue timestamp, run extraction date, inferred calendar cutoff, input
revision and horizon. It does not borrow diagnostics from a reconstructed fit.
Legacy issues' cutoff is explicitly labeled `issuing_run_calendar`, and their
revision is labeled `graph_input_revision`. Historical extractions do not expose
issues recorded after their extraction date. A Combined issue is selected only
from a coherent complete bundle; separate component issues are never passed off
as an originally issued Combined value.

The old weekly current issue remains PHP 450,773.19 for comparison. Genuinely
future weekly points remain PHP 1,218,092.93, PHP 1,252,891.65 and PHP 1,273,186.50.
The correction changes which current outlook is displayed, not the archived issue.

### Current-year outlook conditions on recorded months

Previously, the current year used a January-origin reconstruction of PHP
21,742,071.21 beside October-origin future-year projections, although recorded
2026 revenue through October 28 already totals PHP 51,752,507.08.

The current annual outlook now uses:

```text
sum(recorded completed months)
+ max(model current-month estimate, current-month recorded revenue)
+ sum(model remaining future months)
```

All modeled months come from the same latest closed-month fit. The current-month
floor is disclosed in `annual_outlook`; it does not prorate partial revenue or
alter the fitted parameters. A missing completed month withholds the component
annual outlook and Combined total, rather than filling unknown revenue with
zero. A valid confirmed-zero month remains a known observation. Invalid monthly
values also withhold the sum. Future years remain sums of their own twelve
monthly projections from this same model origin.

This annual point is a **nowcast**: an estimate of a period already underway,
partly based on known actuals. It is not a twelve-month forecast issued in January
and must not be scored or described as one. GET requests can recondition this
annual sum on current-month records using saved fitted parameters; they never
optimize a model or mutate its saved findings. Legacy `demo2026` scope retains
its separate frozen pre-2026 training origin and does not condition predictions
on its displayed demonstration outcomes.

In the read-only saved-state preview, the current annual outlook is PHP
61,861,363.79: PHP 47,390,249.83 in completed Jan–Sep actuals plus PHP
14,471,113.96 in Oct–Dec estimates. Regular contributes PHP 18,013,218.51 and
feeding PHP 43,848,145.28. Neither component currently needs the actual floor;
the regression suite covers activation. All 24 genuinely future targets across
the nine views match their previous saved values exactly, and all 240 archived
issue rows retain identical hashes. This preview prohibits model optimization
and uses a database-enforced read-only transaction.

### Pattern analysis distinguishes unknown from observed zero

The pattern ledger previously created `regular_revenue=0` for months containing
only feeding or unclassified invoices. Correlations, year-over-year comparisons
and benchmarks then treated these as observed regular-sales zeroes. The unfinished
current month also entered correlations/YoY while being excluded from benchmarks.

All three analyses now use the same completed months containing recorded regular
invoices. An explicit zero-value regular invoice remains an observation. Missing
regular months break contiguous correlation/benchmark history; ledger rows and
recorded totals remain unchanged. Additive `analysis_periods` explains exclusions.
This descriptive invoice-only analysis does not consume coverage attestations
and continues to report `coverage_verified=false`.

Graph engine identity advances to `centered-beta-sarima-v5`; pattern analysis
advances to `regular-sales-patterns-v4`. Existing runs/issues are retained rather
than rewritten. A tracked offline refresh creates the updated artifacts.

### Source-policy wording is accurate

The feeding report formerly claimed all synthetic sales were excluded. Its
warning now accurately states that legacy demo references and unclassified
sales are excluded; ordinary local records are included without independently
verifying their provenance. Filtering behavior and records are unchanged.

## Accuracy evidence: keep the populations and engines separate

| Strict regular model / evaluation population | Targets | SARIMA WAPE | Selected baseline WAPE |
| --- | ---: | ---: | ---: |
| Earlier monthly run, original Jan–Dec 2025 | 12 | 10.6433% | 11.3810% |
| Current monthly run, original Oct–Dec 2025 subset | 3 | 13.0308% | 14.9103% |
| Current monthly run, generated Jan–Sep 2026 subset | 9 | 4.2426% | 10.1363% |
| Current monthly run, all held-out outcomes | 12 | 5.9045% | 11.0390% |
| Earlier weekly run, original Sep 29–Dec 22, 2025 | 13 | 26.8135% | 20.2992% |
| Current weekly run, generated Jul 27–Oct 19, 2026 | 13 | 8.2603% | 6.1348% |

Lower WAPE is better. These are saved retrospective evaluations, not new
acceptance tests. Monthly and weekly scores are different tasks. The three/nine
monthly subgroups are descriptive slices of the existing held-out folds; they
were not used to select a replacement model. A strong generated-data score does
not validate the generator against the real dairy business. Weekly SARIMA loses
to its baseline both before and after the generated extension.

The earlier original-only monthly model wins seven of twelve target months,
with a 6.48% reduction in total absolute error over its baseline. Leaving one
existing target out of that descriptive comparison changes relative skill from
about -4.26% to +11.39%; the apparent improvement is sensitive to a small sample.
This is not a retrained cross-validation result or a confidence interval.

Strict primary regular-model scores cannot be assigned to the graph's different
provisional feeding/weekly models, to the Combined sum, or to annual nowcasts.
The graph's three visible completed points provide only limited, horizon- and
origin-dependent descriptive errors. Its existing `evaluation` grouping remains
compatible; it can contain different issued horizons and is not an approval gate.
Compare identical target periods, origins, horizons and source populations when
making a model-quality claim. This follows the rolling-origin evaluation and
forecast-accuracy principles in [Forecasting: Principles and Practice, Chapter
5](https://otexts.com/fpp3/toolbox.html).

## Frontend boundary

Frontend `d4d35f44` was pulled as requested; no frontend source was edited. Its
green connector begins at the final orange point, so part of that display bridge
lies over a completed bucket. That is a connector, not a future forecast issued
for the past. Period membership must use `is_current` and `end_date`: October 26
is the start of the current week ending November 1.

`FRONTEND_HANDOFF.md` specifies origin labels, latest outlook versus issued
comparison, annual-nowcast explanation, true model-family labels, and neutral
connector treatment. Backend fields and legacy statuses remain compatible;
current-period prediction values intentionally adopt the selected fresh-outlook
semantics. Frontend adoption is still required for the explanation/comparison UI.

## Verification and operational result

The complete forecasting and dummy-cleanup suite passed **199/199 tests** in
406.374 seconds in a fresh disposable PostgreSQL database with an isolated cache;
that database was removed. The origin-focused suite also passed 42 tests,
overlapping the full suite. Seven new invoice-pattern semantics regressions and
17 new graph-origin/annual-outlook regressions are included in the complete run.

Django system checks, Python syntax, migration consistency/status and whitespace
checks pass; no migrations were applied. Subsequent cleanup restored the exact
original sales-input revision and preserved every retained row across 46 models.
Original-only tracked maintenance job 22 and ten authenticated/permission HTTP
checks passed, including all graph periods directly and through the unchanged
frontend proxy. Actuals, current-year missingness and model readiness reflect the
original-only dataset. See `DATA_CLEANUP_REPORT.md` for the operational record.

Local aggregate figures and reproducible read-only extraction scripts are kept
in the gitignored, private `forecast_experiments/pattern-review-20261028-640f6449/`
folder (directory 0700, files 0600). `data-patterns.png`/`.svg` compares recorded
patterns and generated assumptions; `origins-and-evaluation.png`/`.svg` compares
origins and error populations. Raw customer names are not included in figures.

## What is required before an academic or deployment readiness claim

1. Reconcile original invoice dates/classifications and certify actual coverage;
   do not infer zero demand from a missing invoice. Review feeding billing cycles
   against delivery/program schedules without inventing a schedule.
2. Retain source provenance for the demonstration extension and explain its
   assumptions in the defense. Historical generator performance cannot supply
   real prospective outcomes.
3. Evaluate the actual intended graph engine at matching origins/horizons against
   simple baselines. Preserve a genuinely untouched test period when proposing
   model changes; diagnose residuals and inspect uncertainty rather than forcing
   seasonality or changing thresholds after seeing the test result.
4. Complete the existing reviewed prospective coverage and acceptance requirements.
   At this review there are zero MonthlyCoverage, ProjectionCoverage and
   MonthlyForecastScore rows, and no evidence was fabricated. `research_only`
   remains appropriate; graph availability is not production approval.
5. Adopt the frontend handoff, then verify chart labels/tooltips, neutral boundary
   connector, partial-period explanations and separate issued comparisons across
   all nine views.
6. Handle hosting, HTTPS/origins/secrets, production database/cache, scheduled jobs,
   native Windows ACL verification if applicable, restore rehearsal and existing
   security/dependency follow-ups under the deployment plan. Intentional local
   development configuration remains unchanged.
