#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python 3 is required. Run: xcode-select --install' >&2
    exit 1
fi
exec python3 "$ROOT/scripts/manage.py" install "$@"
