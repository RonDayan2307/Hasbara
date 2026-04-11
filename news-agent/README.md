# News Agent - Stage 1

This stage collects articles from configured media sites, saves AI-readable article files, reviews them with a local Ollama model, groups relevant items into time-bounded topics, and writes a report to the Desktop.

## Modular Files

The project is now controlled by plain text files:

- [runtime_settings.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/runtime_settings.txt)
  OS type, model name, output paths, batching, and thresholds.
- [review_criteria.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/review_criteria.txt)
  The scoring questions. Each criterion is scored from 1 to 10.
- [source_sites.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_sites.txt)
  Site name, homepage URL, and number of articles to analyze.
- [source_rules.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_rules.txt)
  Optional per-source deny/prefer rules to avoid topic hubs, newsletters, and other non-article pages.

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

## Runtime Settings

Edit [runtime_settings.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/runtime_settings.txt#L1).

Important fields:

- `os_type=macos` or `os_type=windows`
- `local_ai_model=gemma4:e4b`
- `output_dir=../reports`
- `report_mode=local` or `report_mode=model`
- `review_mode=batch` or `review_mode=single`
- `review_batch_size=2`
- `criteria_path=config/review_criteria.txt`
- `source_config_path=config/source_sites.txt`
- `source_rules_path=config/source_rules.txt`

Recommended defaults:

- `review_mode=single` for the current one-by-one workflow
- `review_batch_size=2` is a safer default for Gemma than `5`
- `max_review_stories=3` keeps the run small while tuning
- `report_mode=local` for faster step 6
- `output_dir=../reports` keeps generated reports inside the main `Hasbara` folder
- `max_article_paragraphs=4`
- `max_body_chars=900`
- `review_num_predict_per_story=80`
- `num_ctx=2048`

## Review Criteria

Edit [review_criteria.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/review_criteria.txt#L1).

Format:

```txt
criterion_name | material description
```

Example:

```txt
israel_political_relevance | politically relevant to Israel
anti_zionist_content | anti-Zionist
misinformation_risk | misleading, false, manipulative, or clearly unverified
virality | viral, highly shareable, or likely to spread quickly
```

For each criterion, the local model is asked the equivalent of:

`Does this article contain <material description> material?`

and returns a score from `1` to `10`.

## Source Sites

Edit [source_sites.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_sites.txt#L1).

Format:

```txt
Site Name | Homepage URL | Number of Articles | Language (optional) | Orientation (optional) | Priority (optional)
```

Example:

```txt
AP World | https://apnews.com/world-news | 3 | English | wire_service | 3
Reuters World | https://www.reuters.com/world/ | 3 | English | wire_service | 3
```

## Source Rules

Edit [source_rules.txt](/Users/ronday/Desktop/Hasbara/news-agent/config/source_rules.txt#L1).

Format:

```txt
Site Name | deny_path=/topic/;/newsletter/ | deny_title=daily edition;monthly update | prefer_path=/israel/;/middle-east/
```

Use this file to keep each source pointed at actual articles instead of hub pages, newsletters, or recurring roundup pages.
For example, the default rules now block Haaretz `analysis` titles during stage 1.

## What Changed In This Iteration

1. Review scoring is now driven by editable text criteria.
2. Review scores are now `1-10` per criterion instead of fixed hardcoded fields.
3. `summary_bullets` was removed from records to reduce waste.
4. The current run mode processes articles one by one instead of bulk review.
5. Step 6 local synthesis now groups by topic and uses the review data rather than just echoing a fast placeholder.
6. The review artifact is slimmer and keyed by `story_id`.
7. Source configuration now supports a plain text site list.
8. Source selection now rotates across sources instead of draining only the top-priority site first.
9. Invalid model JSON is now logged with raw debug output under `data/debug/`.
10. Empty model responses now also create debug artifacts, and reports call out heuristic fallback usage explicitly.

## Output Files

- `data/articles/YYYY-MM-DD/articles_*.jsonl`
  Extracted article text and metadata.
- `data/reviews/YYYY-MM-DD/reviews_*.jsonl`
  Review results keyed by `story_id`, plus topic and cross-check data.
- `data/runs/YYYY-MM-DD/run_*.json`
  Run manifest with artifact paths and counts.
- `data/topics.json`
  Time-bounded topic memory.
- `../reports/hasbara-stage1-report-*.md`
  Human-readable report.

## Key Files

- [main.py](/Users/ronday/Desktop/Hasbara/news-agent/src/main.py)
- [settings.py](/Users/ronday/Desktop/Hasbara/news-agent/src/settings.py)
- [criteria.py](/Users/ronday/Desktop/Hasbara/news-agent/src/criteria.py)
- [scraper.py](/Users/ronday/Desktop/Hasbara/news-agent/src/scraper.py)
- [analyzer.py](/Users/ronday/Desktop/Hasbara/news-agent/src/analyzer.py)
- [memory.py](/Users/ronday/Desktop/Hasbara/news-agent/src/memory.py)
- [writer.py](/Users/ronday/Desktop/Hasbara/news-agent/src/writer.py)
