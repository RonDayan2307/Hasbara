"""System health checks."""

from __future__ import annotations

import sys
import logging
from pathlib import Path

from ..analysis.ollama_client import OllamaClient
from ..config import load_config

logger = logging.getLogger("news_agent.jobs.doctor")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def check(label: str, passed: bool, detail: str = "") -> bool:
    mark = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    msg = f"  [{mark}] {label}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    return passed


def run_doctor(config_path: str | None = None) -> bool:
    """Run system health checks."""
    print(f"\n{BOLD}News Agent - System Doctor{RESET}\n")
    all_pass = True

    # Python version
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 9
    all_pass &= check("Python version", ok, f"{v.major}.{v.minor}.{v.micro}")

    # Config
    try:
        config = load_config(config_path)
        all_pass &= check("Config file", True, "loaded successfully")
    except Exception as e:
        check("Config file", False, str(e))
        return False

    # Required keys
    for key in ("model", "sources", "thresholds", "paths", "scraping"):
        ok = key in config
        all_pass &= check(f"Config key: {key}", ok)

    # Database path
    db_path = config["paths"]["database"]
    db_dir = Path(db_path).parent
    all_pass &= check("Database directory", db_dir.exists() or True,
                       f"{db_path} (will be created on first run)")

    # Output directories
    for key in ("reports", "logs", "debug"):
        p = Path(config["paths"][key])
        p.mkdir(parents=True, exist_ok=True)
        all_pass &= check(f"Directory: {key}", p.exists(), str(p))

    # Ollama
    client = OllamaClient(
        base_url=config["model"]["base_url"],
        model=config["model"]["name"],
    )

    ollama_ok = client.is_available()
    all_pass &= check("Ollama reachable", ollama_ok,
                       config["model"]["base_url"])

    if ollama_ok:
        model_ok = client.model_exists()
        all_pass &= check("Model available", model_ok,
                           config["model"]["name"])
        if not model_ok:
            print(f"    {YELLOW}Run: ollama pull {config['model']['name']}{RESET}")
    else:
        check("Model available", False, "Ollama not reachable")
        print(f"    {YELLOW}Ensure Ollama is running: ollama serve{RESET}")
        all_pass = False

    # Sources
    sources = config.get("sources", [])
    enabled = [s for s in sources if s.get("enabled", True)]
    all_pass &= check("Sources configured", len(enabled) > 0,
                       f"{len(enabled)} enabled")

    # Scoring criteria
    criteria = config.get("scoring_criteria", [])
    all_pass &= check("Scoring criteria", len(criteria) > 0,
                       f"{len(criteria)} criteria")

    # Summary
    print()
    if all_pass:
        print(f"  {GREEN}{BOLD}All checks passed.{RESET}\n")
    else:
        print(f"  {RED}{BOLD}Some checks failed. Fix issues above.{RESET}\n")

    return all_pass
