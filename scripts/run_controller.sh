#!/usr/bin/env bash
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD"
mkdir -p logs
export SD_LOG="${SD_LOG:-$PWD/logs/controller.jsonl}"
echo "controller events go to $SD_LOG (watch them with: tail -f $SD_LOG, or open the dashboard)"
exec venv/bin/ryu-manager --observe-links controller.sd_controller
