#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BUILD_SCRIPT="$REPO_ROOT/tools/build_package.py"

PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 was not found on PATH. Install Python 3.10+ or set PYTHON=/path/to/python." >&2
  exit 1
fi

ARGS=("$BUILD_SCRIPT")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --onefile)
      ARGS+=("--onefile")
      ;;
    --clean)
      ARGS+=("--clean")
      ;;
    --skip-install)
      ARGS+=("--skip-install")
      ;;
    *)
      ARGS+=("$1")
      ;;
  esac
  shift
done

"$PYTHON_BIN" "${ARGS[@]}"
