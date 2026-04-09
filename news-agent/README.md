# News Agent - Stage 1

This stage collects articles from configured media outlets, saves AI-readable files, reviews each article with a local Ollama/Gemma model, attaches relevant items to time-bounded topic memory, and writes a concise report to the Desktop.

Default local model:

- `gemma4:31b`

## What Stage 1 Does

1. Scrapes article links from `config/sources.json`.
2. Extracts readable article text and saves it as JSONL under `data/articles/`.
3. Sends each article to the local model for triage:
   - political relevance to Israel
   - explicit anti-Zionist content
   - misinformation or verification risk
   - virality, when metrics exist
4. Summarizes articles that are worth review.
5. Cross-checks reviewed articles against existing time-bounded topic memory in `data/topics.json`.
6. Saves a human report to the Desktop and AI-readable review files under `data/reviews/` and `data/runs/`.

The agent is designed to stay source-faithful: it preserves uncertainty and treats misinformation scoring as "needs verification", not as a final truth verdict.

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

Make sure Ollama is running and the local model is available:

```bash
ollama list
```

## Run

### macOS / Linux

```bash
source .venv/bin/activate
export OLLAMA_MODEL=gemma4:31b
python src/main.py
```

### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
$env:OLLAMA_MODEL = "gemma4:31b"
python src\main.py
```

Expected output:

- progress logs from `[1/7]` to `[7/7]`
- report file under `Desktop/HasbaraReports/`
- AI-readable artifacts under `news-agent/data/`

## Useful Settings

These environment variables work on macOS, Linux, and Windows:

- `OLLAMA_MODEL`: local model name. Default: `gemma4:31b`
- `OLLAMA_URL`: Ollama chat endpoint. Default: `http://localhost:11434/api/chat`
- `NEWS_MAX_REVIEW_STORIES`: max articles reviewed per run. Default: `5`
- `NEWS_MAX_ARTICLE_PARAGRAPHS`: max extracted paragraphs per article. Default: `6`
- `NEWS_MAX_BODY_CHARS`: max body characters sent to Gemma per article. Default: `2400`
- `NEWS_TOPIC_WINDOW_DAYS`: topic memory window. Default: `14`
- `NEWS_OUTPUT_DIR`: custom report output folder. Default: `Desktop/HasbaraReports/`

PowerShell example:

```powershell
$env:NEWS_MAX_REVIEW_STORIES = "2"
$env:NEWS_OUTPUT_DIR = "$HOME\Desktop\HasbaraReports"
python src\main.py
```

macOS/Linux example:

```bash
export NEWS_MAX_REVIEW_STORIES=2
export NEWS_OUTPUT_DIR="$HOME/Desktop/HasbaraReports"
python src/main.py
```

## Configure Sources

Edit `config/sources.json`.

Each source supports:

- `name`: source name used in reports.
- `language`: source language for filtering and analysis.
- `orientation`: source orientation label, if known.
- `priority`: numeric priority. Higher sources are reviewed first.
- `homepage`: page to scan for article links.
- `base_url`: base URL for relative links.
- `max_links`: maximum links to collect from that source.
- `link_selector`: CSS selector for article links.

Example:

```json
{
  "name": "Example News",
  "language": "English",
  "orientation": "wire_service",
  "priority": 4,
  "homepage": "https://example.com/world",
  "base_url": "https://example.com",
  "max_links": 5,
  "link_selector": "a.article-link"
}
```

## Output Files

The filenames avoid characters that cause trouble on Windows or macOS.

- `data/articles/YYYY-MM-DD/articles_*.jsonl`: extracted article text and metadata.
- `data/reviews/YYYY-MM-DD/reviews_*.jsonl`: model reviews and topic/cross-check results.
- `data/runs/YYYY-MM-DD/run_*.json`: complete machine-readable run.
- `data/topics.json`: time-bounded topic memory.
- `Desktop/HasbaraReports/hasbara-stage1-report-*.md`: human-readable report.

## Key Files

- `src/main.py`: stage-1 pipeline entrypoint.
- `src/scraper.py`: source loading, link collection, article extraction.
- `src/analyzer.py`: Ollama/Gemma review and final report synthesis.
- `src/memory.py`: time-bounded topic memory and cross-checking.
- `src/writer.py`: AI-readable artifacts and Desktop report output.
- `config/sources.json`: source list, language/orientation labels, and priority.
