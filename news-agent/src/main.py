#!/usr/bin/env python3
"""News Agent entry point."""

import sys
from pathlib import Path

# Ensure the src directory is in the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from news_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
