# News Agent (Ollama + Claude Code workflow)

This project scrapes news pages, summarizes with local Ollama, and writes a `.txt` digest to your Desktop.

Default model:
- `gemma4:31b`

## 1) Setup

```bash
cd "/Users/ronday/Desktop/ollama testing/news-agent"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Make sure Ollama is running and model exists:

```bash
ollama list
```

## 2) Smallest test first (recommended)

Project is currently in smoke-test config by default:
- `config/sources.json` has one source (`AP World`) with one link.
- Summarizer defaults to one story.

Run:

```bash
cd "/Users/ronday/Desktop/ollama testing/news-agent"
source .venv/bin/activate
export OLLAMA_MODEL=gemma4:31b
export NEWS_MAX_SUMMARY_STORIES=1
export NEWS_MAX_ARTICLE_PARAGRAPHS=4
export NEWS_MAX_BODY_CHARS=1200
export OLLAMA_NUM_PREDICT=180
export OLLAMA_TIMEOUT_SECONDS=900
python src/main.py
```

Expected output:
- progress logs `[1/4] ... [4/4]`
- file on Desktop: `news_digest_YYYY-MM-DD.txt`

## 3) Scale up gradually

Step A: keep one source, increase story count

```bash
export NEWS_MAX_SUMMARY_STORIES=2
export OLLAMA_NUM_PREDICT=260
python src/main.py
```

Step B: add more sources in `config/sources.json` and increase each `max_links`.

Step C: summarize more stories

```bash
export NEWS_MAX_SUMMARY_STORIES=3
python src/main.py
```

If it becomes slow, lower:
- `NEWS_MAX_BODY_CHARS`
- `NEWS_MAX_ARTICLE_PARAGRAPHS`
- `OLLAMA_NUM_PREDICT`

## 4) Key files

- `src/main.py`: pipeline entrypoint.
- `src/scraper.py`: scraping + article extraction.
- `src/summarizer.py`: Ollama call + summarization prompt.
- `src/writer.py`: writes Desktop digest file.
- `config/sources.json`: source list and selectors.

## 5) Model wiring

Runtime model is controlled by your Python app, not by Claude Code itself:
- `OLLAMA_MODEL` env var (default `gemma4:31b`) in `src/summarizer.py`
- `OLLAMA_URL` env var (default `http://localhost:11434/api/chat`)
