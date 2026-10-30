#!/usr/bin/env bash
# Install the complete GitHub wheel in a private venv. No Docker, npm or sudo.
set -euo pipefail
rf_version="${RF_HARNESS_VERSION:-0.2.0}"
rf_prefix="${RF_HARNESS_PREFIX:-$HOME/.local/share/rf-skill-harness}"
rf_python="${RF_HARNESS_PYTHON:-python3}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix) rf_prefix="$2"; shift 2 ;;
        --python) rf_python="$2"; shift 2 ;;
        --version) rf_version="$2"; shift 2 ;;
        -h|--help) echo 'Usage: bash install.sh [--prefix DIRECTORY] [--python PYTHON] [--version VERSION]'; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
if [[ ! "$rf_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo 'Version must be a release number such as 0.2.0.' >&2; exit 2
fi
rf_base="https://github.com/wa200508/RF-Skill-Harness/releases/download/v$rf_version"
rf_wheel="rf_skill_harness-$rf_version-py3-none-any.whl"
rf_download="$(mktemp -d)"
trap 'rm -rf "$rf_download"' EXIT
curl -fsSL --retry 3 "$rf_base/$rf_wheel" -o "$rf_download/$rf_wheel"
curl -fsSL --retry 3 "$rf_base/SHA256SUMS" -o "$rf_download/SHA256SUMS"
"$rf_python" - "$rf_download" "$rf_wheel" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
root, name = Path(sys.argv[1]), sys.argv[2]
rows = [line.split() for line in (root / 'SHA256SUMS').read_text().splitlines()]
expected = next((digest for digest, filename in rows if filename == name), None)
if expected is None or sha256((root / name).read_bytes()).hexdigest() != expected:
    raise SystemExit('Release checksum mismatch; installation stopped.')
PY
"$rf_python" -m venv "$rf_prefix/venv"
"$rf_prefix/venv/bin/python" -m pip install --disable-pip-version-check "$rf_download/$rf_wheel"
"$rf_prefix/venv/bin/rf-world" doctor
printf '\nInstalled. Launch with:\n  "%s/venv/bin/rf-world" serve --workspace ./my-rf-skill --transmit\n' "$rf_prefix"
