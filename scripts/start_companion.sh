#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${RF_HARNESS_PYTHON:-}" ]]; then
  rf_harness_python=$(command -v "$RF_HARNESS_PYTHON")
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
  rf_harness_python="$VIRTUAL_ENV/bin/python"
else
  rf_harness_python=$(command -v python3)
fi
rf_harness_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$rf_harness_root/scripts/launch_companion_browser.py" ]]; then
  exec "$rf_harness_python" "$rf_harness_root/scripts/launch_companion_browser.py" "$@"
fi
# The same script can be downloaded alone. Fetch only stdlib host code, pinned
# to the pulled image, so a fresh host needs neither a clone nor GNU Radio.
rf_companion_image=${RF_COMPANION_IMAGE:-ghcr.io/wa200508/airsim-rf:rf-skill-companion-browser}
docker pull "$rf_companion_image"
rf_companion_revision=$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$rf_companion_image")
if [[ ! "$rf_companion_revision" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'The Companion browser image must declare a full source revision.' >&2
  exit 1
fi
rf_companion_cache=${XDG_CACHE_HOME:-$HOME/.cache}/rf-skill-companion/$rf_companion_revision
mkdir -p "$rf_companion_cache/scripts" "$rf_companion_cache/src/rf_skill_harness"
for rf_companion_file in scripts/launch_companion_browser.py src/rf_skill_harness/catalog.py; do
  rf_companion_download=$(mktemp "$rf_companion_cache/download.XXXXXXXX")
  if ! curl -fsSL "https://raw.githubusercontent.com/wa200508/RF-Skill-Harness/$rf_companion_revision/$rf_companion_file" -o "$rf_companion_download"; then
    rm -f "$rf_companion_download"
    exit 1
  fi
  mv "$rf_companion_download" "$rf_companion_cache/$rf_companion_file"
done
touch "$rf_companion_cache/src/rf_skill_harness/__init__.py"
exec "$rf_harness_python" "$rf_companion_cache/scripts/launch_companion_browser.py" \
  --image "$rf_companion_image" --no-pull "$@"
