#!/usr/bin/env bash
# Export both application images and the files required to run the complete stack.
set -euo pipefail
umask 077

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
output_dir=${1:-"$repo_dir/../docker-exports"}
mkdir -p -- "$output_dir"
output_dir=$(cd -- "$output_dir" && pwd)
release_name="rosario-stack-$(date -u +%Y%m%dT%H%M%SZ)"
archive="$output_dir/$release_name.tar.gz"
if [[ -e "$archive" ]]; then
    echo "Archive already exists: $archive" >&2
    exit 1
fi

docker image inspect rosario-dairy-backend:local >/dev/null
docker image inspect rosario-dairy-frontend:local >/dev/null
# /tmp may be RAM-backed on development laptops; stage large images on disk.
staging=$(mktemp -d "$output_dir/.rosario-export.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
package="$staging/$release_name"
mkdir -p "$package/docker"
cp -- "$repo_dir/compose.yaml" "$repo_dir/docker.env.example" \
    "$repo_dir/DOCKER.md" "$repo_dir/FRONTEND_DOCKER_HANDOFF.md" \
    "$repo_dir/FRONTEND_HANDOFF.md" "$package/"
cp -- "$repo_dir/docker/nginx.conf" "$repo_dir/docker/frontend.nginx.conf" \
    "$repo_dir/docker/compose.frontend.example.yaml" "$package/docker/"
docker image inspect rosario-dairy-backend:local rosario-dairy-frontend:local \
    --format '{{json .RepoTags}} {{json .Id}} {{json .Os}} {{json .Architecture}}' > "$package/IMAGES.txt"
docker image save --output "$package/stack-images.tar" rosario-dairy-backend:local rosario-dairy-frontend:local
(cd "$package" && sha256sum stack-images.tar > IMAGES.sha256)
tar -czf "$archive" -C "$staging" "$release_name"
(cd "$output_dir" && sha256sum "$release_name.tar.gz" > "$release_name.tar.gz.sha256")
printf 'Handoff archive: %s\nChecksum: %s.sha256\n' "$archive" "$archive"
