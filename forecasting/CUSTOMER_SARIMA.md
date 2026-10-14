Run the recorded-purchase customer experiment:

```bash
venv/bin/python manage.py compare_customer_sarima --workers 4
```

The supplied transactions are trusted. A date without a recorded purchase is
represented as zero recorded revenue. No gap is labeled a terminated customer
relationship, missing data, or a reason to delete the customer. No purchases,
large transactions, or inactive periods are removed or changed.

Customer selection uses 2023–2024 only. Eligible linked accounts have at least
nine positive-purchase calendar months, eighteen positive-purchase dates, and
180 days between first and last purchase within that prefix. The eight largest
eligible accounts by recorded prefix revenue are modeled individually. All
remaining accounts, new accounts, and unassigned sales stay in a pooled series.
Customer IDs group existing linked aliases; the experiment does not merge or
rename accounts. A customer remains in the selected group throughout testing.

Both experimental arms use the same dates: initial history 2023–2024, rolling
validation January–June 2025, and rolling retrospective test July–December 2025.
The aggregate arm forecasts all regular revenue as one series. The customer arm
adds the individual account forecasts and the pooled remainder. Both arms add
the identical frozen feeding model from the earlier original-grid comparison.
This isolates the effect of customer grouping. Both actuals and predictions
reconcile; no sales are omitted from combined WAPE.

The search uses 18 daily seasonal specifications per series: 56-/91-/365-day
windows, level/log-mean/log-median transformations, and two SARIMA order pairs
with seven-day seasonality. Forecasts are summed into calendar weeks or months.
For monthly series with recorded positive activity starting in January 2023,
four direct annual-seasonal monthly candidates are also eligible. Pre-entry
zero padding does not certify a customer's annual seasonal history. Weekly
series use daily seven-day seasonal models rather than fitting an additional
annual weekly grid to sparse account histories.

Each series' model minimizes validation MAE, equivalent to WAPE ranking on the
same dates. The preferred structure is frozen using combined validation MAE
before either arm is tested. Per-account WAPE is undefined when all actuals are
zero; MAE remains visible. Such accounts and their errors remain in the totals.

This is exploratory retrospective evidence: 2025 was already inspected. There
are only six monthly and twenty-five weekly test periods. Report both arms,
even when the validation-selected arm loses on test data. A lower group WAPE
does not mean every individual customer was forecast accurately. The 15% target
applies to combined WAPE; a regular-sales result below 15% does not establish it.

Private outputs in `forecast_experiments/customer_sarima/` include full results,
summary and prediction CSVs, customer-selection audit, and customer-level errors.
Candidate and reporting-period checkpoints allow interrupted runs to resume.
Saved results use version `customer-sarima-v1`, separate from production.
The source hash includes daily customer allocations in addition to the real
aggregate source, so customer reassignment invalidates the experiment even if
the daily total remains unchanged. Source hashes are rechecked before saving.

The first training-only cohort consists of Skye Dairy, Milk Joy, Arnold Berse,
Ginos, Magdalena, JCG, PCC-UPLB, and WTC. The monthly test result is 12.19% regular
WAPE for the customer arm versus 20.85% for the aggregate arm. Combined WAPE is
35.89% versus 34.28% with the same feeding predictions. Validation selected the
aggregate structure, so the customer result is an exploratory comparison rather
than an automatically promoted winner. Production is unchanged.

The completed experiment is snapshot 32. The weekly test result is 22.85%
regular WAPE for the customer arm versus 25.79% for the aggregate arm. Combined
weekly WAPE improved from 59.98% to 58.21%. Weekly validation selected the customer
structure; monthly validation selected the aggregate structure. Both structures
and all model settings were frozen before scoring either test. The feeding
forecast is identical across arms (monthly WAPE 53.81%, weekly WAPE 84.68%).

All 55 forecasting tests passed. Real aggregate and customer-allocation hashes
were checked unchanged after the experiment. No production forecast, customer
relationship status, or transaction was modified. The monthly customer-based
regular result is below 15% retrospectively, but combined results are not.
