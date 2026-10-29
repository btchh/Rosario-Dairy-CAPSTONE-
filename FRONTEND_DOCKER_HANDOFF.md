# Backend image and frontend integration

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
returns customer `total_spent`; its frontend display remains pending. Rebuild
the backend image and generate a fresh archive after backend changes, so the
shared image matches the handoff.

## Add the frontend image to the same stack

Build the frontend as its own image tagged `rosario-dairy-frontend:local`. The
provided example assumes an Nginx-based image with the built Vite assets at
`/usr/share/nginx/html`. The frontend teammate maintains that Dockerfile in the
frontend repository.

Build Vite with `VITE_API_BASE_URL=/backend` and no overriding `VITE_API_URL`.
These values are embedded at build time. The supplied frontend Nginx configuration
forwards `/backend/` to the backend gateway on the Compose network and serves
client-side routes through `index.html`. This matches the existing frontend's
`/backend` API base without changing its application code.

```bash
docker compose --env-file .env.docker \
  -f compose.yaml -f docker/compose.frontend.example.yaml up -d --no-build
curl -i http://localhost:5173/backend/accounts/user/
```

Open `http://localhost:5173`. The proxied user request should also return 401
before login. Test login, one stock change and one sale from the UI. A different
frontend image tag can be selected with `FRONTEND_IMAGE` in `.env.docker`.

Browsers use `localhost:5173`; service names such as `gateway` are for container
traffic. No database credentials belong in the frontend image. While the frontend
still runs with native Vite, its existing proxy to `localhost:8000` works with the
backend-only stack.

These are local development defaults. Remote access, production TLS/origins and
database roles require the deployment configuration described in `DOCKER.md`.
