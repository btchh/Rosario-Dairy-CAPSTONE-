# Complete application Docker handoff

The archive contains both the backend and frontend images, Compose configuration,
Nginx gateway configuration, environment template and integration contract.
Database records, login accounts, private backups and `.env.docker` are excluded.
PostgreSQL, Redis and the gateway Nginx image are pulled separately on first start.

## Build and export from source

Keep the backend and frontend checkouts as sibling folders. The default frontend
path is `../SarimaHoesButFrontend`; set `FRONTEND_CONTEXT` in `.env.docker` to use a
different location. The frontend changes live on `beta/docker-handoff`, based on
upstream `7d59b2ef`. Use the matching backend with the `total_spent` customer field.

```bash
docker compose --env-file .env.docker build api frontend
docker compose --env-file .env.docker up -d --no-build
bash docker/export-handoff.sh
```

The exporter writes a `rosario-stack-*.tar.gz` archive and SHA-256 checksum to
`../docker-exports/`. Send both files. Both application images are Linux amd64;
the receiving machine needs amd64 support or must rebuild for its architecture.

## Load on another machine

Install Docker with Compose. Check the downloaded archive against its `.sha256`
file, extract it, and open the extracted folder. Then:

```bash
sha256sum -c IMAGES.sha256
docker image load --input stack-images.tar
cp docker.env.example .env.docker
chmod 600 .env.docker
```

Replace the two secret placeholders in `.env.docker` with new values for this
machine. Source checkouts are unnecessary when using the loaded images:

```bash
docker compose --env-file .env.docker up -d --no-build
docker compose --env-file .env.docker ps -a
docker compose --env-file .env.docker exec api python manage.py createsuperuser
```

Open **http://localhost:5173**. The backend API is also reachable at
`http://localhost:8000`; its root path has no homepage. The new database starts
without existing accounts or business records. Create a local administrator with
the command above, then create staff through User Management if needed.

The one-shot migration service should exit 0; API, database, Redis and frontend
should be healthy. The worker performs forecast maintenance at startup and then
at midnight Asia/Manila. Empty history leaves estimates unavailable.

## Frontend integration

The frontend image builds Vite assets with `/backend` as the API base and serves
them through an unprivileged Nginx process on container port 8080. Its proxy sends
`/backend/` to `gateway:8080`, stripping the prefix. Both images run application
processes without root; the frontend filesystem is read-only with temporary files
under `/tmp`. No Node development server runs in the final frontend image.

The existing native Vite workflow remains available. For native development use
`npm ci`, `npm run typecheck`, `npm test`, and `npm run dev` in the frontend repo.
The complete local Compose stack occupies port 5173; stop its frontend service
before starting native Vite on that port.

## Verify and update

```bash
curl -i http://localhost:5173/backend/accounts/user/
curl -I http://localhost:5173/backend/static/admin/css/base.css
docker compose --env-file .env.docker exec api python docker/healthcheck.py
```

Expect 401 from the unauthenticated user endpoint and 200 for the stylesheet.
Log in as admin and staff, record a sale, verify stock and customer totals,
then check report previews/PDFs. Forecast controls expose centered history and
seasonal outlook, separate original issues, model evidence and annual calculations.
They do not grant production approval or create missing observations.

For a new archive, stop gateway/API/worker/frontend before loading the new
`stack-images.tar`, then run `up -d --no-build --force-recreate`. For source
updates, build both `api` and `frontend` first. Restart all backend processes and
retire older business API payloads when deploying the customer total field:

```bash
docker compose --env-file .env.docker exec api python manage.py shell -c "from config.data_cache import invalidate_data_cache; invalidate_data_cache()"
```

Keep the Compose project name consistent to reuse the same named database volume.
Ordinary `down` retains data; `down --volumes` deletes it. See `DOCKER.md` for
storage and deployment details. Real public hosting still requires the matching
HTTPS proxy, backend production settings, database roles and SMTP configuration.
