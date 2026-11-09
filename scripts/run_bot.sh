#!/usr/bin/env bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
cd "$DIR"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv .venv
    uv pip install --python .venv -r requirements.txt
fi

echo "Starting MaguraBot Sniper Engine on port 8000..."
exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
