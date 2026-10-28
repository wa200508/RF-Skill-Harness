#!/usr/bin/env bash
# Standalone deployment: no clone, host DSP dependencies or capture required.
set -euo pipefail
rf_harness_image=${RF_HARNESS_IMAGE:-ghcr.io/wa200508/airsim-rf:rf-skill-harness}
docker pull "$rf_harness_image"
rf_harness_revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$rf_harness_image")
if [[ ! "$rf_harness_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'The workbench image must declare a full source revision.' >&2
  exit 1
fi
rf_harness_cache=${XDG_CACHE_HOME:-$HOME/.cache}/rf-skill-harness/$rf_harness_revision
mkdir -p "$rf_harness_cache/scripts" "$rf_harness_cache/src/rf_skill_harness"
rf_harness_source=https://raw.githubusercontent.com/wa200508/RF-Skill-Harness/$rf_harness_revision
for rf_harness_file in scripts/launch_container.py src/rf_skill_harness/catalog.py; do
  rf_harness_download=$(mktemp "$rf_harness_cache/download.XXXXXXXX")
  if ! curl -fsSL "$rf_harness_source/$rf_harness_file" -o "$rf_harness_download"; then
    rm -f "$rf_harness_download"
    exit 1
  fi
  mv "$rf_harness_download" "$rf_harness_cache/$rf_harness_file"
done
touch "$rf_harness_cache/src/rf_skill_harness/__init__.py"
if [[ -n "${RF_HARNESS_PYTHON:-}" ]]; then
  rf_harness_python=$(command -v "$RF_HARNESS_PYTHON")
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  rf_harness_python=$VIRTUAL_ENV/bin/python
else
  rf_harness_python=$(command -v python3)
fi
exec "$rf_harness_python" "$rf_harness_cache/scripts/launch_container.py" \
  --image "$rf_harness_image" --no-pull lab "$@"
