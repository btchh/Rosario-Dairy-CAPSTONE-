# Backend image and frontend integration

## Current local release — 2026-10-31

Frontend `f6e4546a` and the matching backend are deployed at
`http://localhost:5174`. Migration `accounts.0005_loginsession` is applied;
existing users must sign in again. Idle logout is active at 15 minutes without
interaction, with a one-minute warning and confirmed continuation. Gmail SMTP
remains configured and its authentication check passes.

Both images are tagged `:idle-f6e4546a-20261031` and `:local`:

- `rosario-dairy-frontend`: `sha256:6c5730791ff212cc8c9bc7b75808dfec8726b054125ab96bfff021291af1875f`.
- `rosario-dairy-backend`: `sha256:f67dc73bcfe16bb4692c0c55895b47c53b4ade1d6b938a0d82f8f72c8c2adf06`.

Previous images are retained as `:before-idle-f6e4546a-20261031`. All 614 frontend
tests, TypeScript, 736 backend tests (one platform skip), health and authenticated
admin/staff browser checks passed. The private full database backup was restored
successfully in isolated PostgreSQL. All 19 existing business-table contents
were unchanged. See `BACKEND_VALIDATION.md` for scope/evidence.

Pagination remains partial; it is the only open frontend request in
`FRONTEND_HANDOFF.md`. No new image export/archive was created by this rollout.

## Sharing an image

Share the exported archive with the frontend teammate. It contains the backend
image, Compose stack, Nginx configuration and environment template. Database
contents, accounts, private backups and `.env.docker` are not included. PostgreSQL,
Redis and Nginx images are pulled separately, so the first start needs internet.

## Export on the backend machine

After the stack builds and passes the smoke checks in `DOCKER.md`:

```bash
bash docker/export-handoff.sh
```

The archive and its SHA-256 checksum are written to `../docker-exports/`, outside
the repository. Send both files. `IMAGE.txt` records the image ID, OS and CPU
architecture. The current Linux x86-64 build needs amd64 support on the receiving
machine; a native build for another architecture requires the backend source.

## Start on the receiving machine

Live updates add an internal `events` service to this stack. Rebuild/export a
current backend image before sending this Compose file; older images do not
contain Uvicorn or the SSE endpoint. The gateway waits for both API and event
service health. See the [versioned live-event contract](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/7a75a0ab/FRONTEND_HANDOFF.md#live-updates-across-sessions--2026-10-30)
for the client integration implemented in frontend `63ecff33`. The optional frontend Nginx example now
forwards `/backend/events/` without buffering; preserve this behavior in any
additional hosting proxy. Database migrations are unchanged by live updates.

The product-only cleanup requires migration
`inventory.0012_product_only_inventory`. Rebuild/export the backend image to
include it; old archives do not contain the cleanup. Back up an existing database
and stop API/forecast workers before migrating. Migration stops if retired
ingredient/supplier data exists; archive it explicitly before retrying. Product,
sales and customer records are retained. See the [completed cleanup reference](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/7a75a0ab/FRONTEND_HANDOFF.md#product-only-inventory-cleanup--2026-10-30)
for temporary empty legacy-list responses for older clients.

Install Docker with Compose. Verify the downloaded archive against its checksum,
extract it, and open the extracted directory. Then run:

```bash
sha256sum -c IMAGE.sha256
docker image load --input backend-image.tar
cp docker.env.example .env.docker
chmod 600 .env.docker
```

Replace the two secret placeholders in `.env.docker` with new values for this
machine. Then start the stack using the loaded image:

```bash
docker compose --env-file .env.docker up -d --no-build
docker compose --env-file .env.docker ps -a
docker compose --env-file .env.docker exec api python manage.py createsuperuser
curl -i http://localhost:8000/accounts/user/
```

The user request should return 401 before login. The migration job should exit 0
and the API should become healthy. The database starts empty; create a new local
administrator. Keep the Compose project name and directory consistent when
updating so the same database volume is reused. See `DOCKER.md` for storage and
update instructions. The archive supports `--no-build`; rebuilding requires the
backend Git repository.

The API/UI requests are documented in [FRONTEND_HANDOFF.md](FRONTEND_HANDOFF.md),
which is also included in newly exported archives. In particular, the backend
returns customer `total_spent`; frontend `9986a769` displays it and derives an
open profile from the refreshed customer list. Rebuild
the backend image and generate a fresh archive after backend changes, so the
shared image matches the handoff.

## Add the frontend image to the same stack

**Implemented 2026-10-30; rebuilt for frontend `63ecff33`.** All container packaging lives
in the backend repository; the frontend checkout is used as build input and
its application files are unchanged. Keep the checkouts alongside each other:

```text
CodingProjects/
  SarimaHoes/               # Run the commands below from here.
  SarimaHoesButFrontend/    # Frontend source and package-lock.json.
```

`docker/frontend.Dockerfile` uses Node 24 to run `npm ci` and `npm run build`,
then copies only the built Vite output into an Nginx runtime image. The build
copies an explicit set of source/configuration/assets from the named frontend
context; frontend `.env` files, `.git` and host `node_modules` are not copied.
The build override uses Docker's
[named build contexts](https://docs.docker.com/build/concepts/context/#named-contexts),
requiring BuildKit and Docker Compose 2.17 or newer. The preparation machine's
Compose 5.6.1 supports this.

Build the image from the backend directory:

```bash
docker compose --env-file .env.docker \
  -f compose.yaml \
  -f docker/compose.frontend.example.yaml \
  -f docker/compose.frontend.build.yaml build frontend
```

The image is tagged `rosario-dairy-frontend:local`. Set `FRONTEND_SOURCE` in
`.env.docker` if the frontend checkout is elsewhere; relative paths resolve from
the backend Compose directory. Set `FRONTEND_IMAGE` to choose another image tag.

For an already running backend, add just the frontend container:

```bash
docker compose --env-file .env.docker \
  -f compose.yaml -f docker/compose.frontend.example.yaml \
  up -d --no-deps frontend
```

`--no-deps` preserves the running backend services. For a fresh or updated full
stack, first build the current backend image as described in `DOCKER.md`, then
run the same command with `up -d --no-build` and without `--no-deps frontend`.
The backend migration/event services must be current before that full startup.

The default UI address is `http://localhost:5173`. If native Vite already uses
that port, set `FRONTEND_PORT=5174` in `.env.docker` and open
`http://localhost:5174`. The preparation machine uses 5174 for Docker alongside
native Vite on 5173. Check it with the port selected in your environment:

```bash
docker compose --env-file .env.docker \
  -f compose.yaml -f docker/compose.frontend.example.yaml ps frontend
curl -i http://localhost:5173/backend/accounts/user/
```

Use port 5174 in the curl URL when configured. The proxied user endpoint returns
401 before login. A running frontend becomes healthy once Nginx serves
`index.html`; the container uses a read-only filesystem with temporary Nginx
directories mounted separately. Route fallback serves `index.html` when opening
or refreshing an application URL.

The browser sends API requests to its own `/backend` path. Frontend Nginx strips
that prefix and forwards to `gateway:8080` inside the Compose network. The image
build fixes `VITE_API_BASE_URL=/backend`; these Vite values are embedded at build
time, so changing an environment variable on an existing container does not
rebuild the JavaScript. Database credentials stay with Django. The frontend
proxy supports unbuffered `/backend/events/`; frontend `63ecff33` includes the
client integration described in the versioned live-event contract above. The local backend image
and events service were also updated, and both proxied streams passed HTTP
verification. Subsequent isolated browser QA passed real staff-to-admin updates,
preserved date filters, offline recovery and logout. The remaining pagination
performance request is documented in `FRONTEND_HANDOFF.md`.

After frontend pulls, repeat the image build, then `up -d --no-deps frontend`.
This changes the UI container while retaining the existing backend/database.
For deployment without source, send/load the frontend image separately and
start using only the first two Compose files; the normal backend export includes
the frontend packaging files but does not embed the frontend image. A separately
hosted Cloudflare Pages frontend remains an alternative to this container.

These addresses bind to loopback for local use. On OVHcloud, put an HTTPS reverse
proxy/domain in front of the UI, preserve the API/event routes, and apply the
production environment/role settings in `DOCKER.md` before public deployment.

## Optional private package with existing business data

The separate `rosario-backend-with-data-*.tar.gz` package contains the backend
image, `database.dump`, `restore-database.sh`, `DEV_LOGIN.txt` and
`START_HERE.md`, plus the configuration/handoff files. It contains private
customer and business records and newly generated development credentials.
Share it directly with the intended developer and keep it outside Git.
The standard `docker/export-handoff.sh` archive continues to exclude data.

Verify the downloaded archive's checksum, extract it, then follow START_HERE.md.
Load backend-image.tar and create a fresh private .env.docker using the included
template. With Docker and Bash available, run:

```bash
bash restore-database.sh
```

The script backs up the target Docker database before restoring the snapshot,
clears the stack's cache, applies migrations and starts the backend. It replaces
existing target data, so use it for importing a snapshot, not ordinary startup.
Use the credentials in DEV_LOGIN.txt through the unchanged frontend development
server on port 5173. The backend remains on localhost:8000. Each developer runs
an independent data copy; changes are not synchronized between machines.
The copied dataset's original account passwords and old session/reset tokens
are disabled/removed. Original accounts on the sender's local and normal Docker
databases retain their credentials. No public database port is opened.

The latest development snapshot removes recorded 2026 sales and stock activity.
Forecasts for 2026 remain visible in charts and reports; they are projections,
separate from recorded transactions. Saved models and issued forecasts remain
intact. Use the newest package; older archives are superseded.

The load/input audit and its measured limits are documented in
`BACKEND_VALIDATION.md`. Checkout/order request UUIDs are supported by the
backend; frontend `9986a769` sends request UUIDs and offers saved-submission
recovery. The live-event client has since been implemented; its contract is linked above.
A Git pull and a previously exported image are separate
versions: load the newest image package to receive the audit fixes.

Report previews and PDF exports now have independent request budgets; forecast
polling does not consume either allowance. Repeated manual report refreshes
return 200 with `refresh_status: coalesced` while the rebuild lock/cooldown remains
in force. Current frontend callers already handle this success response; see
the [versioned report-refresh contract](https://github.com/btchh/Rosario-Dairy-CAPSTONE-/blob/7a75a0ab/FRONTEND_HANDOFF.md#report-availability-and-refresh-handling) for the exact response fields and limits.
