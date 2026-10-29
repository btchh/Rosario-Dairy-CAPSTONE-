#!/usr/bin/env bash
# Export the backend image and the files required to run/add a frontend service.
set -euo pipefail
umask 077

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output_dir=${1:-"$repo_dir/../docker-exports"}
mkdir -p -- "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
release_name="rosario-backend-$(date -u +%Y%m%dT%H%M%SZ)"
archive="$output_dir/$release_name.tar.gz"
if [[ -e "$archive" ]]; then
    echo "Archive already exists: $archive" >&2
    exit 1
fi

docker image inspect rosario-dairy-backend:local >/dev/null
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
package="$staging/$release_name"
mkdir -p "$package/docker"
cp -- "$repo_dir/compose.yaml" "$repo_dir/docker.env.example" \
    "$repo_dir/DOCKER.md" "$repo_dir/FRONTEND_DOCKER_HANDOFF.md" "$package/"
cp -- "$repo_dir/docker/nginx.conf" "$repo_dir/docker/frontend.nginx.conf" \
    "$repo_dir/docker/compose.frontend.example.yaml" "$package/docker/"
docker image inspect rosario-dairy-backend:local \
    --format '{{json .Id}} {{json .Os}} {{json .Architecture}}' > "$package/IMAGE.txt"
docker image save --output "$package/backend-image.tar" rosario-dairy-backend:local
(cd "$package" && sha256sum backend-image.tar > IMAGE.sha256)
tar -czf "$archive" -C "$staging" "$release_name"
(cd "$output_dir" && sha256sum "$release_name.tar.gz" > "$release_name.tar.gz.sha256")
printf 'Handoff archive: %s\nChecksum: %s.sha256\n' "$archive" "$archive"
