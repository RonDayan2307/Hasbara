#!/usr/bin/env bash
# Stage 1 News Agent — auto-install and run
# Usage: ./run.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "[setup] Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"

REQ_STAMP="$VENV_DIR/.requirements.sha256"
CURRENT_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"

if [ ! -f "$REQ_STAMP" ] || [ "$(cat "$REQ_STAMP")" != "$CURRENT_HASH" ]; then
    echo "[setup] Installing dependencies..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    printf "%s" "$CURRENT_HASH" > "$REQ_STAMP"
else
    echo "[setup] Dependencies already match requirements.txt"
fi

echo "[run] Starting News Agent..."
python src/main.py
