#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROBE_SCRIPT="${REPO_ROOT}/tools/lianli_wireless_probe.py"

if [ ! -f "${PROBE_SCRIPT}" ]; then
  echo "找不到 lianli_wireless_probe.py: ${PROBE_SCRIPT}" >&2
  exit 1
fi

if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="${PYTHON}"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi

if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH}"
else
  export PYTHONPATH="${REPO_ROOT}"
fi

exec "${PYTHON_BIN}" "${PROBE_SCRIPT}" "$@"

