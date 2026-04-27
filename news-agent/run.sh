#!/usr/bin/env bash
# News Agent runner script
# Usage: ./run.sh [command] [options]
# Examples:
#   ./run.sh            # Run once
#   ./run.sh run        # Run once
#   ./run.sh --watch    # Watch mode
#   ./run.sh doctor     # Health check
#   ./run.sh cleanup    # Data cleanup
#   ./run.sh sources    # List sources
#   ./run.sh alerts     # Show recent alerts

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate venv if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

python src/main.py "$@"
