# News Agent - Stage 1

This project is the Stage 1 pipeline for the Hasbara monitoring system. It collects articles from configured media sites, extracts machine-readable text, reviews each story with a local Ollama model, groups relevant items into time-bounded topics, and writes a markdown report inside the main `Hasbara` workspace.

The current implementation is optimized for a single analyst running locally with a local model and plain-text configuration files.

## Core Flow

Each run goes through these stages:

1. Load runtime settings and review criteria
2. Run a strict Ollama health check
3. Collect article candidates from configured sources in round-robin order — via RSS feeds where available, falling back to homepage scraping. Only articles from the last 2 hours are collected.
4. Skip already-seen URLs (cross-run deduplication via `seen_urls.json`) and previously rejected URLs (avg score < 3, via `rejected_urls.json`)
5. Extract article text with source-aware rules and deterministic body extraction
6. Skip non-English articles (language detection via langdetect)
7. Review each story one by one with the local model
8. Classify each review: **save** (avg > 6 or any score >= 8), **reject** (avg < 3, URL recorded), or **normal**
9. Fall back conservatively if structured model output fails
10. Attach worthy stories to topic memory (expired topics are pruned on save)
11. Write JSONL/JSON artifacts and a markdown report (reports older than 30 days are auto-archived)

## Configuration Files

The pipeline is controlled by plain-text files:

- [runtime_settings.txt](config/runtime_settings.txt)
  Runtime behavior, model name, thresholds, paths, and output budgets.
- [review_criteria.txt](config/review_criteria.txt)
  The scoring criteria. Each criterion is asked: `Does this article contain <material> material?`
- [source_sites.txt](config/source_sites.txt)
  Source name, homepage URL, max links, language, orientation, priority, and optional `rss_url`.
- [source_rules.txt](config/source_rules.txt)
  Per-source deny/prefer rules to avoid topic hubs, autoplay shells, newsletters, explainers, and similar noise.

## Setup

Use Python 3.9 or newer.

### macOS / Linux

```bash
cd news-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
cd news-agent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Ollama Setup

Ollama must be running locally before each pipeline run.

```bash
# Pull the model (one-time setup)
ollama pull gemma4:e4b

# Verify the model responds correctly
ollama run gemma4:e4b "Say only OK"

# Start Ollama if not already running
ollama serve
```

If `ollama serve` says the port is already in use, Ollama is already running — you can skip that step.

## Running

```bash
cd /Users/ronday/Desktop/Hasbara/news-agent
source .venv/bin/activate
python src/main.py
```

Or use the provided script which auto-installs dependencies:

```bash
./run.sh
```

## Important Runtime Settings

Key fields in [runtime_settings.txt](config/runtime_settings.txt):

| Setting | Default | Notes |
|---------|---------|-------|
| `local_ai_model` | `gemma4:e4b` | Must match a pulled Ollama model |
| `report_mode` | `local` | `local` = structured renderer; `model` = AI-synthesized |
| `max_body_chars` | `1500` | Max article text fed to the model |
| `max_article_paragraphs` | `4` | Max paragraphs extracted per article |
| `review_num_predict_per_story` | `256` | Token budget for structured model output |
| `num_ctx` | `3072` | Context window size |
| `min_usable_review_ratio` | `0.8` | Below this → run marked `degraded` |
| `topic_window_days` | `14` | How long topics stay active in memory |
| `output_dir` | `../reports` | Report output directory |

## Review Criteria

Active criteria (in [review_criteria.txt](config/review_criteria.txt)):

| Criterion | What it detects |
|-----------|----------------|
| `israel_political_relevance` | Politically relevant to Israel, Israeli policy, or Israeli interests |
| `antisemitic_content` | Antisemitic targeting or hatred toward Jews |
| `anti_zionist_content` | Anti-Zionist framing or denial of Israel's right to exist |
| `misinformation_risk` | Misleading, false, or manipulative claims |
| `virality` | Viral or highly shareable content |
| `narrative_delegitimization` | Delegitimizing Israel's existence or right to self-defense |
| `source_credibility` | Low-credibility, state-controlled, or disinformation outlet |

Additional criteria (`incitement_to_violence`, `geopolitical_escalation`) can be activated by uncommenting lines in `review_criteria.txt`.

## Sources

10 outlets are configured (in [source_sites.txt](config/source_sites.txt)):

| Source | Priority | Notes |
|--------|----------|-------|
| Times of Israel | 5 | Israeli center |
| Jerusalem Post | 5 | Israeli right |
| Haaretz English | 5 | Israeli left; many articles paywalled — body filter silently drops short extracts |
| Ynet News | 5 | Israeli center |
| AP World | 4 | Wire service |
| France 24 English | 4 | International; RSS feed for Middle East section |
| BBC Middle East | 3 | RSS feed configured |
| Guardian Middle East | 3 | RSS feed configured |
| Al Jazeera English | 2 | RSS feed configured; Cloudflare may block homepage fallback |
| Middle East Eye | 2 | RSS feed configured |

RSS feeds are used as the primary link source when the `rss_url` field is set in `source_sites.txt`, with homepage scraping as automatic fallback. To add an RSS feed for a source, append `| rss_url=https://...` to its line.

## Output Files

- `data/articles/YYYY-MM-DD/articles_*.jsonl` — Extracted article text and metadata
- `data/reviews/YYYY-MM-DD/reviews_*.jsonl` — Structured review results with topic and cross-check data
- `data/runs/YYYY-MM-DD/run_*.json` — Run manifest with health status, counts, and source health
- `data/topics.json` — Time-bounded topic memory (expired topics pruned automatically on each save)
- `data/review_cache.json` — Namespaced cache of successful model reviews
- `data/seen_urls.json` — Cross-run URL store; skips already-processed articles (14-day TTL)
- `data/rejected_urls.json` — URLs with avg score < 3.0; skipped without model call on future runs (14-day TTL)
- `../reports/hasbara-stage1-report-*.md` — Analyst-facing markdown report
- `../reports/archive/` — Reports older than 30 days are auto-archived here

## Trust and Degraded Runs

Reviews are labeled by method:

- `model` — fresh model output
- `cached` — returned from review cache
- `heuristic_fallback` — model failed; conservative scoring applied

Runs are marked `degraded` when the usable review ratio falls below `min_usable_review_ratio`. Source failures, extraction failures, and candidate skips are recorded in the run manifest and the markdown report.

## Maintenance / Data Reset

```bash
cd /Users/ronday/Desktop/Hasbara/news-agent

# Clear topic memory (start fresh topic grouping)
rm -f data/topics.json

# Clear review cache (force re-review of all articles)
rm -f data/review_cache.json

# Clear debug payloads from model failures
rm -rf data/debug/

# Archive all current reports manually
mkdir -p ../reports/archive && mv ../reports/hasbara-stage1-report-*.md ../reports/archive/
```

## Tests

```bash
cd /Users/ronday/Desktop/Hasbara/news-agent
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -p "test_*.py"
```

## Key Modules

- [main.py](src/main.py) — Pipeline orchestrator
- [settings.py](src/settings.py) — Config loader and validation
- [contracts.py](src/contracts.py) — TypedDict data shapes
- [telemetry.py](src/telemetry.py) — Source health tracking
- [scraper.py](src/scraper.py) — RSS + homepage link collection, article extraction, language filtering
- [analyzer.py](src/analyzer.py) — Ollama review with 3-tier fallback
- [memory.py](src/memory.py) — Time-bounded topic memory with automatic expired-topic pruning
- [report_renderer.py](src/report_renderer.py) — Markdown report rendering
- [writer.py](src/writer.py) — Artifact writer with automatic report archiving

## Operator Notes

See [STAGE1_OPERATOR.md](../STAGE1_OPERATOR.md) for the operator guide and [WORKLOG.md](../WORKLOG.md) for the implementation log.
