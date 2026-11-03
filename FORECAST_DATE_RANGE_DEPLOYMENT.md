# Forecast fixes deployed — 4 November 2026

Live: https://rosariodairy.duckdns.org.

Release: `/opt/rosario/rosario-release-2026.11.04-forecast.1`.
Backend code: `29b5b3c41c7c9247d95873ac976ccb2f84850e30` (pushed to origin/main).
Frontend code: `729a37c76bdb7def380c029f093ad2d7811c6bd3` (committed locally;
the upstream GitHub repository rejected the configured account's write access).
Both exact source archives are included in the release package.

Images:

- `rosario-dairy-backend:release-2026.11.04-forecast.1`,
  `sha256:ff27fe96615572b10561da27fa3775025fc0293f848a3f04efdbc891b22999b0`.
- `rosario-dairy-frontend:release-2026.11.04-forecast.1`,
  `sha256:a52bf380a0ed78b7cd9d34078dba18fd9a0b02ef90f52e89d5c39a655904fdae`.

Validation passed: 709 frontend tests, TypeScript, production builds, 63 unique
targeted backend tests, and Chromium against both local and public APIs. The
public browser checked six date ranges (past, future, mixed, unavailable future,
cross-year weeks and whole years), nine period/view switches stable from the
first frame, and the gradient boundary's exact alignment with its forecast dot.
No JavaScript exception or failed forecast HTTP response was observed. All six
public entry-point asset hashes matched the deployed frontend image.
Temporary verification sessions and outstanding tokens were deleted by exact ID.

No schema migration was run. All 18 business-table fingerprints matched before
and after deployment, including 4,835 sales transactions. A database backup was
taken while backend writers were stopped, retained on the VPS and downloaded
to protected local preparation storage with matching SHA256:
`17fb99071c002826ba4abc73f61fac012a25ff55456b909c5480e19fd929ac21`.
This verifies the copy, not a full restore rehearsal.

The previous release and images remain intact. The application-only rollback is:

```sh
sudo bash /opt/rosario/private/forecast-fix-20261104/deploy-or-rollback.sh rollback
```

That restores the previous Compose wrapper and application images and reloads
both proxies. It does not reverse the yearly SI migration or restore the database.
The recovery directory also retains the prior wrapper, database backup,
preflight/checksum logs and business-table fingerprints.

The longer requested timeline does not manufacture missing forecasts. Saved
availability, missing-sales warnings and planning-only status remain truthful.

## Animation follow-up — 4 November 2026

The latest frontend is `b2a06d9b6a9f3cfe9685a051555b23338436cab0`, image
`rosario-dairy-frontend:release-2026.11.04-forecast.2`, ID
`sha256:3f0c51ead8aad41a15698bfcaf5f19bf5a9efda25c5f8e136f4d0b25182295bc`.
This supersedes the original frontend's disabled animations: the forecast and
recorded-sales curves now use their original Recharts transitions. The forecast
still renders as one curve, with no independent boundary connector.

Only the frontend container changed; the API, events and forecast-worker
container IDs and images were verified unchanged. All 709 frontend tests,
TypeScript and the production build passed. Chromium on local and public
sites verified intermediate animation frames and settled geometry for all nine
period/view combinations, including rapid view reversals. Public entry-point
asset hashes matched the new image. Temporary check sessions/tokens were
removed by exact ID. No sales or inventory request was submitted.

The active release remains `rosario-release-2026.11.04-forecast.1`, with its
frontend image pin updated. The exact newer frontend source archive, manifest,
previous image pins and previous private environment are retained under
`/opt/rosario/private/forecast-animation-20261104/`. Frontend-only rollback:

```sh
sudo bash /opt/rosario/private/forecast-animation-20261104/deploy-or-rollback.sh rollback
```

The frontend upstream still denies the configured GitHub account write access;
the local commit and combined frontend patch are retained for handoff.
