# News Agent

Local CLI news monitoring agent for reputational-risk situational awareness. Scrapes public/RSS-accessible English news sources, analyzes articles with a local Ollama model, groups stories into narratives, detects reputational risk, writes markdown reports, and prints terminal alerts.

**Stage 1**: CLI-only, local-only. No cloud LLMs, no paid APIs, no social media, no dashboard.

## Quick Start

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai/) installed and running
- Model: `gemma4:e4b`

### Setup (macOS/Linux)

```bash
cd news-agent

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy environment config
cp .env.example .env

# Ensure Ollama is running and pull the model
ollama serve &  # if not already running
ollama pull gemma4:e4b

# Verify setup
python src/main.py doctor
```

### Setup (Windows PowerShell)

```powershell
cd news-agent

# Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Copy environment config
Copy-Item .env.example .env

# Ensure Ollama is running and pull the model
# Start Ollama from its installed location, then:
ollama pull gemma4:e4b

# Verify setup
python src\main.py doctor
```

## Usage

### Run once (scrape, analyze, report)

```bash
python src/main.py
# or
python src/main.py run
# or
./run.sh
```

### Watch mode (run every hour)

```bash
python src/main.py --watch
# or
python src/main.py watch
```

### Health check

```bash
python src/main.py doctor
```

### Other commands

```bash
python src/main.py sources    # List configured sources
python src/main.py alerts     # Show alerts from last run
python src/main.py report     # Show latest report
python src/main.py cleanup    # Run data retention cleanup
```

### Debug mode

```bash
python src/main.py --debug run
```

## Output

- **Reports**: `reports/` directory. Each run produces a timestamped markdown file + `latest.md`.
- **Database**: `data/news_agent.db` (SQLite)
- **Logs**: `logs/news_agent.log`

## Configuration

Edit `config.yaml` to customize:

- **Model**: Ollama endpoint, model name, timeout, retries
- **Sources**: Add/remove/configure news sources
- **Thresholds**: Scoring thresholds for reporting and alerting
- **Retention**: Data retention periods
- **Scraping**: Request delays, user agent, robots.txt behavior

### Source configuration

Each source supports:
- `name`, `homepage_url`, `rss_url` (optional)
- `enabled`, `language`, `region`
- `orientation` (manually configured editorial leaning)
- `credibility_level` (manually configured)
- `priority` (1=highest)
- `max_links` (max articles per scrape)
- `deny_patterns` / `prefer_patterns` (URL regex filters)

### Scoring

Articles scored 0-10 on 9 criteria. Final score = min(10, average + count_of_criteria_over_7).

| Final Score | Action |
|-------------|--------|
| < 4.0 | Ignored, text deleted |
| 4.0 - 5.9 | Stored short-term |
| 6.0+ | Included in report |
| 8.0+ | High priority topic |
| 9.0+ | Terminal alert |

Override triggers (instant alert): `antisemitic_content >= 9`, `misinformation_risk >= 9` (Israel-related), `hostile_media_narrative >= 9`.

### Data retention

- Article text: 14 days for reported articles, deleted immediately for low-score
- Debug payloads: 14 days
- Topic metadata: retained indefinitely for statistics
- Model cache: 30 days

## Architecture

```
src/
  main.py                       # Entry point
  news_agent/
    cli.py                      # CLI argument parsing
    config.py                   # Configuration loading
    logging_setup.py            # Logging setup
    db/                         # SQLite layer
      connection.py
      schema.py
      repositories.py
      migrations.py
    models/
      contracts.py              # Data classes
    sources/                    # Collection
      source_config.py
      robots.py
      rss.py
      homepage.py
      extractor.py
      canonicalize.py
    analysis/                   # AI analysis
      ollama_client.py
      prompts.py
      scorer.py
      claim_extractor.py
      fallback.py
      cache.py
    topics/                     # Topic grouping
      grouper.py
      lifecycle.py
      comparison.py
    reports/
      markdown_renderer.py
    alerts/
      terminal.py
    jobs/                       # Pipeline orchestration
      pipeline.py
      scheduler.py
      cleanup.py
      doctor.py
    utils/
      time.py
      text.py
      hashing.py
      json_repair.py
```

## Limitations (Stage 1)

- English-only
- No social media monitoring
- No cloud LLMs or paid APIs
- No web dashboard or push notifications
- No backfill capability
- Single-user local operation
- No Docker support
- Topic grouping quality depends on local model capability
- No automated tests yet

## Future expansion points

- Cloud LLM fallback (designed as swappable provider)
- Telegram/Slack/email notifications
- Social media monitoring layer
- Web dashboard / narrative map UI
- Search API integration
- Multi-language support
- Docker deployment
- Automated test suite

## Troubleshooting

1. **"Ollama not reachable"**: Start Ollama with `ollama serve`
2. **"Model not available"**: Run `ollama pull gemma4:e4b`
3. **Degraded run**: Model was unavailable; keyword-based fallback was used
4. **No articles collected**: Check source URLs, internet connection, or rate limiting
5. **Empty reports**: Articles may not meet the 6.0 score threshold

Run `python src/main.py doctor` to diagnose issues.
