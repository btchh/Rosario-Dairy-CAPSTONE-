# Forecast display dates — 4 November 2026

`GET /api/forecasting/graph/?period=monthly&scope=real&start_date=2025-01-15&end_date=2027-06-10`
returns every overlapping calendar bucket. Weekly buckets run Monday–Sunday;
monthly and yearly buckets cover whole months and years. The response echoes
`requested_range` and retains today's `as_of`. Both dates are required, can be
past or future, and cannot be combined with an explicit historical `as_of`.
Requests above 1,200 buckets are rejected rather than silently truncated.
The ordinary centered, seasonal and historical `as_of` contracts remain supported.

Recorded revenue comes from the eligible ledger through today. Missing revenue
stays null, and genuine recorded zero remains zero. Predictions reuse the
current saved centered/seasonal paths, original issued forecasts, or retained
source-validated Regular historical test rows. Combined historical predictions
are not invented from incomplete components. Complete yearly sums of monthly
rolling tests are explicitly marked as such. Original issues take precedence
and retain their issuing provenance. GET does not fit, project new values,
publish or write forecasts. Availability and research-only warnings remain.

The frontend sends target dates instead of using To as `as_of`. A custom range
overrides seasonal/centered display modes; clearing dates restores the selected
ordinary mode. One forecast curve reads the existing past/future values with a
hard orange/green SVG gradient. Actual revenue has its own blue line. Both
curves use Recharts transition animations (restored in the frontend follow-up);
null values still break the path. No separate boundary connector remains.

Validation: 709 frontend tests, TypeScript and production build; 54 existing
graph/origin/finalization backend tests and nine range tests. Chromium against
the local API verified six past/future/mixed calendars, all nine period/view
switches at the first frame, and the gradient boundary aligned with its dot.
Temporary verification sessions/tokens were deleted by exact ID. No business
transaction was submitted.

This release requires no schema migration. Retain the prior release wrapper
and images as the application rollback checkpoint. Restore that wrapper and
restart api/events/forecast-worker/frontend, then reload gateway/frontend
Nginx. Do not reverse the previous SI migration or restore the database for
this application-only rollback.
