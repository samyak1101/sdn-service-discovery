#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"
exec venv/bin/ryu-manager controller.static_controller
