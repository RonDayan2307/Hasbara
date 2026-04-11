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

# Install / update dependencies quietly
echo "[setup] Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "[run] Starting News Agent..."
python src/main.py
