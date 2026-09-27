#!/usr/bin/env sh
set -eu
cd -- "$(dirname -- "$0")"
if [ -n "${PYTHON:-}" ]; then
    exec "$PYTHON" bootstrap.py "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 bootstrap.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python bootstrap.py "$@"
else
    echo "Python 3.11 or newer is required. Install Python, then run this file again." >&2
    exit 1
fi
