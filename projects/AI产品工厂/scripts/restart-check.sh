#!/bin/bash
set -eu
PRODUCT="$(cd "$(dirname "$0")/.." && pwd)"
export FACTORY_CHECK_PRODUCT="$PRODUCT"
exec "$PRODUCT/backend/.venv/bin/python" "$PRODUCT/scripts/restart_probe.py" "$@"
