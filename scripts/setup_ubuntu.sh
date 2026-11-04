#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip python3-matplotlib curl
python3 -m pip install --user uv || python3 -m pip install --user --break-system-packages uv
rm -rf venv
python3 -m uv venv --python 3.9 venv
python3 -m uv pip install --python venv/bin/python "setuptools<66" wheel pbr
python3 -m uv pip install --python venv/bin/python --no-build-isolation ryu==4.34 eventlet==0.30.2
venv/bin/ryu-manager --version
echo "setup finished. Next: venv/bin/python -m tests.run_all"
