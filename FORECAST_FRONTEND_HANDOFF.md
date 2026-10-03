# Sales forecast backend handoff

Updated October 3, 2026 (Asia/Manila). This note covers the forecast backend work in this conversation and the API contract for a frontend teammate. It does not authorize changing the frontend in this task.

## What changed

| Backend file | Change |
| --- | --- |
| `forecasting/historical.py` | New `period_projection` helper. It calculates an unvalidated estimate for the current week, month, or year from matching 2023–2025 periods. |
| `forecasting/serving.py` | Adds `planning_projection` to each forecast report. When sales are stale, it keeps `status: "stale_data"` and `forecast: []`, and omits the 2025 comparison rows from the dashboard response. The saved SARIMA evaluation remains in the database. |
| `reporting/serializers.py` | Exposes `planning_projection`, `status_message`, and `sample_count` through the report preview response. |
| `forecasting/tests.py` | Covers current-period dates and amounts, stale SARIMA behavior, missing historical coverage, and rejection of the removed daily period. |

Other modified backend files in the working tree (`config/settings.py`, `forecasting/contract.py`, `forecasting/data.py`, `forecasting/sarima.py`, and `reporting/pdf.py`) contain work that was already present before this handoff task. Review their diffs separately; they are not required to read `planning_projection` in the frontend.

## API to connect

Use the existing authenticated report preview call:

```http
GET /api/reports/preview/?type=sarima_forecast&period=monthly
```

Allowed `period` values: `weekly`, `monthly`, `yearly`. `daily` returns HTTP 400. The frontend already calls this route from `src/features/reports/api/reports.service.ts`; in local development its browser URL is prefixed with `/backend`, which Vite proxies to Django.

The response is an envelope. Read `response.data.data.planning_projection` in Axios, because the first `.data` is the HTTP body and the second is the report payload. `GET /api/forecasting/forecast/?period=monthly` returns the same forecast payload directly, without the report envelope.

Example report payload fields from the current local database:

```json
{
  "period": "monthly",
  "status": "stale_data",
  "quality_status": "rejected",
  "data_end": "2025-12-31",
  "forecast": [],
  "historical_comparison": [],
  "planning_projection": {
    "date": "2026-10-01",
    "end_date": "2026-10-31",
    "trained_through": "2025-12-31",
    "predicted_revenue": "1368275.80",
    "lower_bound": "898718.70",
    "upper_bound": "5134887.00",
    "point_kind": "historical_median",
    "range_kind": "observed_historical_min_max",
    "sample_count": 3
  }
}
```

The amounts above are a snapshot, not fixed values. `planning_projection` can be `null` when fewer than two historical periods have usable coverage. Amounts are serialized as decimal strings. Dates use `YYYY-MM-DD` in the configured business timezone.

## Meaning of the two forecast fields

- `forecast` is the validated SARIMA publication field. It remains empty while the latest recorded sale is December 31, 2025 and the model cannot publish a current forecast. Do not populate it from `planning_projection` or treat the latter as SARIMA output.
- `planning_projection` is an **unvalidated historical estimate** for the period containing today. Its point value is the median of matching periods in 2023–2025; its bounds are the minimum and maximum observed totals, **not** a confidence interval. Weekly periods run Monday–Sunday and use the same ISO week from each historical year. Monthly and yearly periods use the same calendar month or full calendar year. `sample_count` currently equals 3.
- `data_end` and `trained_through` identify the latest sales evidence. The current database has no recorded 2026 sales, so a 2026 estimate must be labeled as historical and unvalidated.
- `status` and `quality_status` describe the SARIMA publication gate, not the availability of `planning_projection`. A `stale_data` response can still contain a planning estimate. The 2025 SARIMA backtest did not meet the configured quality gate for weekly or monthly forecasts.

## Frontend wiring points for a later task

The current frontend `src/features/dashboard/components/admin/ForecastChart.tsx` reads only `forecast[0]` and displays an amount only when `status === "ready"`. It does not read `planning_projection`, so the current UI correctly withholds the unvalidated amount. The same component hardcodes the text `SARIMA model search · 2025 backtest`; the backend cannot remove that text. It also hardcodes its `ready` explanation as a passed 2025 backtest, so setting `status` to `ready` for a historical estimate would be misleading.

If the team later chooses to display the planning estimate, connect `planning_projection` as a separate state with its own label, dates, and observed range. Keep the SARIMA `forecast` and quality gate distinct. The weekly, monthly, and yearly controls already send the correct `period` query value. There is no Daily control to add.

## Verification

`venv/bin/python manage.py test forecasting.tests.ForecastServingTests reporting.tests --keepdb` passed 25 tests. Both local servers were running when this note was written: Django at `127.0.0.1:8000` and Vite at `127.0.0.1:5173`.
