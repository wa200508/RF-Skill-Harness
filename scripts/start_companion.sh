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
export PYTHONPATH="$rf_harness_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$rf_harness_python" -m rf_skill_harness.companion_gui "$@"
