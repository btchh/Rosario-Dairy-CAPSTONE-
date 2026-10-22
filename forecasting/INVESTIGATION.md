# Forecast horizon investigation — October 22, 2026

This investigation uses the corrected actual dairy invoices only. It does not
change sales, fitted production selections, coverage reviews or forecast approval.
The inspected history has already informed previous research; these are
exploratory comparisons, not an untouched acceptance test.

## Maintenance cost and schedule

Forecast maintenance is now scheduled for **00:00 Asia/Manila each day**. The
active user-systemd timer was reloaded and verified. The hourly and five-minute
startup triggers were removed. A missed midnight run waits for the next midnight,
rather than running during the day. The standalone watcher also defaults to the
next Manila midnight and recalculates that deadline after each run; `--once`
remains an immediate manual/timer operation.

The previous unchanged-data run took 3.214 seconds wall time, 3.717 seconds CPU,
and approximately 146.7 MB peak memory. It reused all saved models. This is not
a full-refit benchmark. The exploratory comparisons below took 17.870 seconds
on this workstation, separately from scheduled maintenance. Neither number is
a production resource guarantee.

## One-month accuracy is not twelve-month accuracy

For a fair horizon comparison, the same existing monthly SARIMA configuration,
`(0,1,0)(1,0,0,12)`, was fitted using only observations earlier than each forecast
origin. Regular research was restricted to the contiguous 2023–2025 monthly span;
the sparse 2022 fragment was not deleted from the database.

| Months forecast from each origin | Error across monthly predictions (WAPE) | Forecast origins |
| --- | ---: | ---: |
| 1 | 10.6433% | 12 |
| 3 | 16.7737% | 10 |
| 6 | 25.2532% | 7 |
| 12 | 21.5794% | 1 |

Targets fall in 2025. Longer horizons have fewer eligible origins, and overlapping
origins repeat some target months; the rows are not independent samples or a
controlled horizon-only experiment. The 12-month path was issued from December
2024 observations. Its **annual-total absolute percentage error is 16.9682%**,
different from the 21.5794% error across its twelve monthly values. The retained
10.6433% score remains correct for a model refreshed before each monthly target.
It does not establish year-ahead accuracy.

Another seasonal AR configuration produced a 13.6519% annual-total error in that
single year while its constituent monthly error was 19.7603%. This is too little
annual evidence to select a yearly winner or claim a dependable sub-15% result.
Yearly and Combined accuracy remain unvalidated even though rough predictions
are available for both.

## Why the new rough model differs

The provisional regular monthly selector uses an earlier six-month validation
path followed by a later six-month test. It selected local-level SARIMA because
its validation error was 30.2120%, versus 34.6055% for AR(1) and 33.0351% for
seasonal AR. The local-level test error was 26.4739%. This selection is separate
from the older monthly rolling validation/test protocol and its 10.6433% result.
Changing the winner after inspecting the later test would invalidate that test
as independent selection evidence.

On the same July–December 2025 test with a June 2025 origin, excluding the early
2022 fragment from AR(1) fitting changed its error from 17.8724% to 14.1528%.
The local-level error remained exactly 26.4739%, so the fragment alone does not
explain the selected model's error. A recent training window is worth evaluating
on earlier validation origins, not automatically adopting from this inspected
six-month outcome. No source rows or production training windows were changed.

## Feeding is sparse, concentrated billing revenue

| Actual observations | Regular | Milk feeding |
| --- | ---: | ---: |
| Invoices | 4,746 | 38 |
| Invoice-bearing months | 38 | 10 |
| Internal missing months | 8 | 4 |
| Invoice-bearing weeks | 160 | 30 |
| Internal missing weeks | 38 | 25 |
| Revenue share of five largest invoices | 1.665% | 30.355% |

The regular counts include two isolated 2022 months. Feeding starts in November
2024; the last observations for both segments are in December 2025. Gaps remain
unknown rather than zeros. Missing periods after the last invoice are not counted
as internal gaps; future paths still propagate through the stale interval.

Feeding's available earlier observations did not permit a six- or twelve-month
holdout under the study's minimum of six observed training periods. This does
not disable rough future projections; it means annual accuracy cannot yet be
measured credibly. Short exploratory tests are unstable: local-level feeding
monthly one-step WAPE was 56.8160% over only four observed targets. An attractive
four-weekly-origin one-step result (13.5221%) worsened to 124.9374% at a four-week
horizon over observed targets. Unknown weeks were excluded from scoring, so this
is conditional recorded-invoice accuracy, not accuracy over all demand.

The rough weekly series also includes the December 29, 2025 bucket, which crosses
the last source-month boundary. The stricter weekly research excludes that
boundary week. These two weekly protocols must not be treated as equivalent;
full-period source boundaries need consistent treatment before future selection
comparisons. No extra invoices or missing-day zeros were invented.

## Next model work, in priority order

1. Match validation to the requested forecast horizon. Use earlier rolling
   origins for weekly, multi-month and annual paths, with later tests kept apart.
   Record both individual-period error and complete-horizon total error.
2. Compare recent contiguous regular training windows and the established monthly
   seasonal configurations using those earlier validation origins. Preserve the
   existing reference model and its original evaluation history.
3. Standardize full-week source boundaries across research and provisional
   models. Preserve recorded history separately; missing coverage is not zero.
4. Retain feeding's rough estimates and explicit sparse/stale warnings while
   collecting actual invoices and coverage evidence. With no schedules supplied,
   do not infer delivery contracts or manufactured program start dates.
5. Accumulate genuinely later outcomes and evaluate uncertainty coverage as well
   as point error. Do not promote a model because one inspected annual total
   happens to be below 15%.

All nine graph combinations remain supported. This investigation changes the
maintenance schedule, not the selected forecasting models or publication gates.

Private reproducibility artifacts are in
`backups/forecast-investigation-20261022/`: `investigate.py` and `results.json`.
Run the script from the backend root with:

```bash
PYTHONPATH=. venv/bin/python backups/forecast-investigation-20261022/investigate.py
```

The export retains source revisions, validation findings, exact historical
origins/targets, missing-period counts, fit failures and candidate comparisons.
The source fingerprint is checked again after the study; it was unchanged.
