#!/usr/bin/env bash
set -euo pipefail
rf_python="${RF_HARNESS_PYTHON:-python3}"
rf_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$rf_script_dir/launch_world.py" ] && [ -f "$rf_script_dir/../src/rf_skill_harness/catalog.py" ]; then
    exec "$rf_python" "$rf_script_dir/launch_world.py" "$@"
fi
rf_image="${RF_WORLD_IMAGE:-ghcr.io/wa200508/airsim-rf:rf-skill-gnuradio-world}"
docker pull "$rf_image"
rf_revision="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$rf_image")"
if [[ ! "$rf_revision" =~ ^[a-f0-9]{40}$ ]]; then
    echo 'Image must declare its full source revision.' >&2
    exit 1
fi
rf_cache="${XDG_CACHE_HOME:-$HOME/.cache}/rf-skill-harness/world/$rf_revision"
mkdir -p "$rf_cache/scripts" "$rf_cache/src/rf_skill_harness"
for rf_file in scripts/launch_world.py src/rf_skill_harness/catalog.py; do
    curl -fsSL "https://raw.githubusercontent.com/wa200508/RF-Skill-Harness/$rf_revision/$rf_file" -o "$rf_cache/$rf_file"
done
exec "$rf_python" "$rf_cache/scripts/launch_world.py" --image "$rf_image" --no-pull "$@"
