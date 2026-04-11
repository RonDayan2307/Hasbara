# News Agent - Stage 1

This project is the Stage 1 pipeline for the Hasbara monitoring system. It collects articles from configured media sites, extracts machine-readable text, reviews each story with a local Ollama model, groups relevant items into time-bounded topics, and writes a markdown report inside the main `Hasbara` workspace.

The current implementation is optimized for a single analyst running locally with a local model and plain-text configuration files.

## Core Flow

Each run goes through these stages:

1. Load runtime settings and review criteria
2. Run a strict Ollama health check
3. Collect article candidates from configured sources in round-robin order
4. Extract article text with source-aware rules and deterministic body extraction first
5. Review each story one by one with the local model
6. Fall back conservatively if structured model output fails
7. Attach worthy stories to topic memory
8. Write JSONL/JSON artifacts and a markdown report

## Configuration Files

The pipeline is controlled by plain-text files:

- [runtime_settings.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/runtime_settings.txt)
  Runtime behavior, model name, thresholds, paths, and output budgets.
- [review_criteria.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/review_criteria.txt)
  The scoring criteria. Each criterion is asked in the form: `Does this article contain <material> material?`
- [source_sites.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_sites.txt)
  Source name, homepage URL, max links, language, orientation, and priority.
- [source_rules.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_rules.txt)
  Per-source deny/prefer rules to avoid topic hubs, autoplay shells, newsletters, explainers, and similar noise.

## Setup

Use Python 3.9 or newer.

### macOS / Linux

```bash
cd news-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

### Windows PowerShell

```powershell
cd news-agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\main.py
```

## Running

Recommended run flow:

```bash
cd /Users/ronday/Desktop/Hasbara/news-agent
source .venv/bin/activate
ollama pull gemma4:e4b
ollama run gemma4:e4b "Say only OK"
python src/main.py
```

If `ollama serve` says the port is already in use, Ollama is usually already running.

## Important Runtime Settings

Current important fields in [runtime_settings.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/runtime_settings.txt#L1):

- `local_ai_model=gemma4:e4b`
- `report_mode=local` or `report_mode=model`
- `max_review_stories=6`
- `max_article_paragraphs=4`
- `max_body_chars=800`
- `review_num_predict_per_story=160`
- `num_ctx=3072`
- `min_usable_review_ratio=0.8`
- `output_dir=../reports`

The pipeline now uses sequential review only. Old batch-review knobs were removed.

## Output Files

- `data/articles/YYYY-MM-DD/articles_*.jsonl`
  Extracted article text and metadata.
- `data/reviews/YYYY-MM-DD/reviews_*.jsonl`
  Structured review results keyed by `story_id`, plus topic and cross-check data.
- `data/runs/YYYY-MM-DD/run_*.json`
  Run manifest with health status, counts, source health, and artifact paths.
- `data/topics.json`
  Time-bounded topic memory.
- `data/review_cache.json`
  Namespaced cache of successful model reviews.
- `data/debug/ollama/*`
  Debug payloads for empty, malformed, or truncated model responses.
- `../reports/hasbara-stage1-report-*.md`
  Analyst-facing markdown report inside the main `Hasbara` folder.

## Trust and Degraded Runs

The pipeline now distinguishes:

- `model` reviews
- `cached` reviews
- `heuristic_fallback` reviews

Runs are marked `degraded` when the usable review ratio falls below the configured threshold. Source failures and extraction failures are also recorded in the run manifest and the markdown report.

## Tests

Run the Stage 1 smoke tests with:

```bash
cd /Users/ronday/Desktop/Hasbara/news-agent
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

## Key Modules

- [main.py](/Users/ronday/Desktop/Hasbara/news-agent/src/main.py)
- [settings.py](/Users/ronday/Desktop/Hasbara/news-agent/src/settings.py)
- [contracts.py](/Users/ronday/Desktop/Hasbara/news-agent/src/contracts.py)
- [telemetry.py](/Users/ronday/Desktop/Hasbara/news-agent/src/telemetry.py)
- [scraper.py](/Users/ronday/Desktop/Hasbara/news-agent/src/scraper.py)
- [analyzer.py](/Users/ronday/Desktop/Hasbara/news-agent/src/analyzer.py)
- [memory.py](/Users/ronday/Desktop/Hasbara/news-agent/src/memory.py)
- [report_renderer.py](/Users/ronday/Desktop/Hasbara/news-agent/src/report_renderer.py)
- [writer.py](/Users/ronday/Desktop/Hasbara/news-agent/src/writer.py)

## Operator Notes

See [STAGE1_OPERATOR.md](/Users/ronday/Desktop/Hasbara/STAGE1_OPERATOR.md) for the operator guide and [WORKLOG.md](/Users/ronday/Desktop/Hasbara/WORKLOG.md) for the implementation log.
