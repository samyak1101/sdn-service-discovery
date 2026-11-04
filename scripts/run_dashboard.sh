#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"
exec python3 -m dashboard.server --logs "${1:-logs}" --port "${PORT:-8080}"
