# Prospective scoring for the three chart views

The midnight/manual maintenance pipeline ends with `refresh_projection_evidence`.
It saves future predictions from the current checksum-verified provisional chart
run, then scores reviewed completed outcomes. It performs no fitting and changes
no model-selection or approval policy. Primary regular-monthly approval and its
existing shadow scores remain separate.

## What gets retained

`ProjectionIssue` stores the first saved prediction for each engine version,
period, view, origin bucket and target. All nine weekly/monthly/yearly ×
regular/feeding/combined paths are supported. Horizon is 1–13 weeks, 1–12 months,
or 1–2 calendar years. The origin bucket is the week/month/year containing the
actual issue timestamp. Repeated refreshes and source corrections within that
bucket cannot replace its predictions. A new origin can issue another forecast
for the same target; evidence is reported separately by horizon.

Only actual issue timestamps before the target starts qualify. The run must
already exist when issuing. Old runs with stale source/target revisions are not
backfilled. Existing private 2026 previews and historical backtests are not
converted into prospective evidence. A missing current run creates no issues.

The frozen comparator repeats the last observed regular/feeding week or month;
yearly uses twelve times the last observed monthly level. Combined adds the two
component comparators. This is an explicitly named simple level baseline, not
an assumption that unobserved periods were zero or that feeding schedules repeat.

## Review actual outcomes

First reconcile the original ledger for the **whole completed target period**.
A named active administrator then records an exact-source attestation, e.g.:

```bash
venv/bin/python manage.py verify_projection_coverage monthly regular 2026-11-01 --admin-username ADMIN --note 'November original ledger reconciled'
venv/bin/python manage.py verify_projection_coverage monthly feeding 2026-11-01 --admin-username ADMIN --note 'November feeding invoices reconciled'
venv/bin/python manage.py refresh_projection_evidence
```

Weeks use their Monday start, months their first date, years January 1. Weekly
reviews cover the entire Monday–Sunday week, including cross-month/year weeks.
Yearly reviews cover the whole calendar year. These attestations do not imply
reviews of other time granularities. Combined scoring requires both segment
reviews for the same period. Nonzero unclassified entries must be resolved first.
An empty period remains unknown unless an operator explicitly confirms a true
zero with `--confirm-zero`. No reviews are generated automatically.

`ProjectionCoverage` binds to the exact source rows, amounts, segment and customer
identity. A correction invalidates its review immediately. `ProjectionScore`
retains old outcomes, but current evidence excludes them until the changed period
is reviewed again and maintenance appends the new score. Scores use predicted
minus actual as signed error. There is no per-observation percentage division
by zero: WAPE uses total absolute error / total absolute actuals; an all-zero
sample has WAPE null, while MAE/bias still describe its errors.

## Inspect evidence

Admin-only, uncached, read-only `GET /api/forecasting/evidence/` returns groups by
engine version, period, view and horizon. Each includes issued/scored/pending
counts, WAPE, MAE, bias, baseline comparison on paired observations, and empirical
nominal interval coverage. GET neither fits, issues nor scores anything.
Predictions awaiting closure and closed periods awaiting review/scoring remain
explicitly pending; they do not contribute to accuracy metrics. Unavailable
metrics are null, never a zero-error claim.

Yearly and combined bounds remain sums of uncalibrated component bounds. Their
observed coverage is diagnostic, not a claim of calibrated joint probability.
Overlapping long-horizon forecasts are correlated. Model selection remains
based on earlier validation; these scores currently monitor outcomes and do not
automatically select a model or promote provisional forecasts. More data or a
new run ID alone does not guarantee a lower future error.
