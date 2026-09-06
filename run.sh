#!/usr/bin/env bash
# mc-print-3d launcher (macOS / Linux). Creates the virtual environment on first run.
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi
if [ $# -eq 0 ]; then
    exec .venv/bin/python -m mcprint gui
else
    exec .venv/bin/python -m mcprint "$@"
fi
