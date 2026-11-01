# OVHcloud VPS 1 preparation — 2026-11-01

Application release: `release-2026.11.01`, frontend `5e390189`. This guide is a
host-preparation supplement; the frozen application archive remains unchanged.
No VPS has been purchased or accessed yet.

## Server and free hostname

Choose OVHcloud VPS 1, Singapore **standard datacentre**, Ubuntu **24.04 LTS
x86_64/amd64**, with SSH-key access. The saved images are linux/amd64. Review
the actual price/resources/commitment shown at checkout; offers can change.
No paid domain or hosting control panel is needed. The plan is a demo workload,
with actual capacity verified after deployment.

Use [DuckDNS](https://www.duckdns.org/), a free DNS service. The user confirmed
claiming **`rosariodairy.duckdns.org`** in their account. Keep the DuckDNS token
private. After OVHcloud supplies the IPv4 address, set
the hostname's IPv4 value in DuckDNS. Leave IPv6 unset unless separately
configured and reachable. A later address change requires a DNS update.

OVHcloud's [VPS FAQ](https://docs.ovhcloud.com/en/guides/bare-metal-cloud/virtual-private-servers/vps-faq)
distinguishes Local Zones, where SMTP is unavailable, from standard VPSs.
Our current email transport uses Gmail STARTTLS on 587. Select a standard
datacentre offer and verify outbound SMTP on the purchased server.

## Prepared local files

- Frozen portable release: `../docker-exports/rosario-release-2026.11.01.tar.gz`
  and its `.sha256` file; see [RELEASE.md](RELEASE.md).
- Public VPS supplement:
  `../docker-exports/rosario-vps-preparation-20261101.tar.gz` and its checksum.
  It contains this guide, host-edge examples and rehearsal evidence, without
  credentials, keys or database data. Follow its `APPLY.md` after verifying the
  original release package.
- Private preparation directory:
  `../private-vps-preparation/ovh-vps1-20261101/` (directory mode 0700).
- `.env.production.pending`: new Django secret, three independent database
  passwords, release image tags and the currently working Gmail SMTP values
  (file mode 0600). The hostname is claimed; VPS creation and DNS pointing remain pending. Confirm
  allowed hosts, health host, CORS and CSRF origins together before deployment.
- `database-preparation.dump` and its checksum: full preparation snapshot.
  Local business changes after this snapshot require a fresh final export.
- `compose.release-images.yaml`: frozen image override from the release archive.
- `ssh/rosario-ovh-ed25519.pub`: dedicated public key for OVHcloud provisioning.
  The matching private key stays on the preparation machine; do not upload it
  to the VPS or include it in either package. Protect that key with its existing
  0600 permissions and the parent directory's 0700 permissions.

Do not commit or include the private directory in the public source package.
Transfer the environment and database files directly over SSH. Do not paste
credentials into chat. Use the public key in OVHcloud's SSH-key option when
available; otherwise install it through the provider's initial-access process.

## First access and host setup

Once the VPS exists, record its public IPv4 address and actual SSH username.
Confirm the SSH host fingerprint against OVHcloud's console before connecting.
Use a sudo-capable account with a working SSH key. Verify a second SSH session
before restricting authentication or enabling a firewall; retain console access.

Install Docker Engine, Buildx and the Compose plugin using
[Docker's official Ubuntu apt repository instructions](https://docs.docker.com/engine/install/ubuntu/).
Install host Nginx, Certbot, Python 3 and UFW using Ubuntu packages. Enable Docker
at boot and verify `sudo docker info` and `sudo docker compose version`.
Use `sudo` consistently for Docker commands unless the operator is deliberately
granted Docker access.

Allow the actual SSH TCP port first, then 80/443 for HTTP/TLS. The supplied
production Compose binds frontend/gateway to loopback and publishes no database,
Redis or application ports. Keep it that way: Docker-published ports can bypass
UFW filtering ([Docker firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).
Verify listening ports and any OVHcloud network firewall as well.

## Transfer and verify

Create an operator-owned directory such as `/opt/rosario` and a separate private
backup directory. Use `scp` or `rsync` over the verified SSH connection to transfer
the release archive/checksum, this supplement and the private environment/backup.
Set directories containing secrets/backups to 0700 and files to 0600.

Verify the archive checksum, extract it, then verify its payload:

```bash
sha256sum --check rosario-release-2026.11.01.tar.gz.sha256
tar -xzf rosario-release-2026.11.01.tar.gz
cd rosario-release-2026.11.01
sha256sum --check SHA256SUMS
sudo docker image load --input images.tar
```

The VPS supplement adds this guide and updated host-edge examples. It does not
change application images. Verify the original package before overlaying the
supplement, which has its own checksums. Copy the confirmed private environment
to `SarimaHoes/.env.production` and chmod 0600; keep the original local copy.

The commands below run from `SarimaHoes`. For clarity, define one shell function
so every command uses the production definition and frozen image override:

```bash
dc() {
  sudo docker compose -p rosario-dairy-production --env-file .env.production \
    -f compose.production.yaml -f ../compose.release-images.yaml \
    --profile frontend "$@"
}
dc config --quiet
```

Do not run `config` without `--quiet` in shared logs; the resolved environment
contains secrets. Do not source the Compose environment as a shell script.

## Restore existing business data before application startup

For this installation, preserve the existing accounts and records. Do not start
all services first: migrations would populate the destination schema before the
full restore. Begin with database/cache and role provisioning only:

```bash
dc up -d --no-build --pull never --wait db redis
dc run --rm -T --no-deps provision
```

Confirm the destination `public` schema contains no tables/sequences/views. If
it already contains application objects, stop and review the existing database;
do not overwrite it or delete its volume. The private dump must come from our
trusted database and its checksum must pass before restoring.

Restore as the **migration role**, retaining neither old ownership nor old
access grants. The example role below must match `MIGRATION_DB_USER`:

```bash
dc exec -T db sh -c \
  'exec pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --role="$1" --no-owner --no-acl --single-transaction --exit-on-error' \
  sh rosario_migrate < /private/path/database-preparation.dump
dc run --rm -T --no-deps provision
dc up -d --no-build --pull never
dc ps -a
dc logs provision migrate preflight
```

The connection uses the container's bootstrap administrator, then `--role`
switches object creation to the migration account. Provisioning is rerun after
restore to establish runtime grants; startup applies pending migrations and
preflight validates restricted privileges. See
[PostgreSQL's restore options](https://www.postgresql.org/docs/18/app-pgrestore.html).
Use the restored administrator account; create a new superuser only for an
intentionally fresh database or a reviewed account-recovery need.

The preparation snapshot is not a final cutover. For final transfer, stop new
local writes and application writers, take and verify a new complete snapshot,
restore/validate it, and keep the old system/backup available for rollback.
Do not silently discard transactions entered after the preparation snapshot.

## DNS and first HTTPS certificate

Confirm the claimed hostname resolves to the VPS IPv4 from outside the VPS.
Prepare `/var/www/letsencrypt/.well-known/acme-challenge/` as a host directory
readable by Nginx. Render `docker/edge.acme.nginx.example.conf` with the confirmed
hostname, install it as the host's `rosario` site, and enable that site. Review the
default Ubuntu Nginx site to avoid conflicting default servers. Run
`sudo nginx -t` before reload. The temporary site serves only ACME files; the app
remains unavailable publicly during setup.

With port 80 reachable and DNS correct, request a certificate:

```bash
sudo certbot certonly --webroot -w /var/www/letsencrypt \
  -d rosariodairy.duckdns.org
```

Use the actual claimed hostname if different. Certbot asks for the operator's
email and terms acceptance. Replace the temporary site with the rendered
`docker/edge.nginx.example.conf`, using the issued certificate paths and
`FRONTEND_PORT` (5173 in the production draft). Validate Nginx and reload.
The updated HTTP site retains the challenge path so webroot renewal works.

Install a root-owned executable deploy hook in
`/etc/letsencrypt/renewal-hooks/deploy/rosario-nginx` that runs
`/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx`. Verify the installed
Certbot renewal timer and run `sudo certbot renew --dry-run --run-deploy-hooks`.
See the official [Certbot webroot and renewal guide](https://eff-certbot.readthedocs.io/en/stable/using.html).
No wildcard or DuckDNS API token is needed for this HTTP validation path.

## Host acceptance and operations

Verify through the actual HTTPS hostname: login, checkout, payment finality,
cancellation/restock, stock adjustment/retry, reports, cashier-to-admin updates,
event reconnects and idle continuation/logout. Confirm the production preflight
completed and database role privileges remain restricted.

Run `dc exec api python manage.py check_smtp` (authentication only). Then request
an OTP using the intended account and confirm delivery. SMTP connection success
alone is not delivery evidence. No server-side email was sent during preparation.

Before accepting public traffic, configure a daily full PostgreSQL custom-format
backup with checksums, private permissions and retention. Copy backups to a
separate machine, and test a restore there. Keep service/backup/forecast failures
visible in monitoring. Verify certificate renewal, server reboot recovery and
the actual demo workload. OVHcloud's included VPS backup is an additional layer;
it does not replace the tested database transfer/restore process.

No change to the frozen application or deferred pagination is required by this
preparation. The target VPS, DNS pointing and public acceptance are pending.

## Rehearsal evidence

An isolated project restored the preparation backup into 55 public tables with
4,841 receipts. All restored objects belonged to the migration account; the
runtime account had table CRUD, no elevated flags, no schema creation and no
table truncation privileges. Production provisioning/migrations/preflight and
frontend/API/event routing passed.

Temporary HTTP certificate bootstrap served the challenge and returned 503 for
the app. The final edge retained challenge serving and redirected other HTTP
requests. A local test certificate verified the HTTPS edge/frontend/gateway/
API/events chain, including a request with an incoming HTTP forwarding scheme.
This was offline/local TLS testing, not an issued public certificate or VPS
verification. No email was sent. Sanitized evidence:
[validation/vps-preparation-20261101.json](validation/vps-preparation-20261101.json).
