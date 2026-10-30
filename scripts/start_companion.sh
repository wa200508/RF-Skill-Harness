#!/usr/bin/env bash
# The experimental browser interface now uses GNU Radio World.
set -euo pipefail
rf_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$rf_script_dir/start_world.sh" ]; then
    exec bash "$rf_script_dir/start_world.sh" "$@"
fi
rf_launcher="$(mktemp)"
trap 'rm -f "$rf_launcher"' EXIT
curl -fsSL https://raw.githubusercontent.com/wa200508/RF-Skill-Harness/main/scripts/start_world.sh -o "$rf_launcher"
bash "$rf_launcher" "$@"
