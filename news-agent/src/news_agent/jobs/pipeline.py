"""Main pipeline: collect, score, group, report."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ..analysis.cache import AnalysisCache
from ..analysis.claim_extractor import extract_claims
from ..analysis.fallback import fallback_score
from ..analysis.ollama_client import OllamaClient
from ..analysis.scorer import score_article
from ..alerts.terminal import print_alerts, print_run_summary
from ..db.connection import get_connection
from ..db.repositories import (
    AlertRepo, ArticleRepo, ArticleTextRepo, CacheRepo,
    ClaimRepo, DebugRepo, RunRepo, ScoreRepo,
    SourceHealthRepo, TopicRepo, TrustedSourceRepo,
)
from ..db.schema import init_schema
from ..models.contracts import RunManifest
from ..reports.markdown_renderer import render_report
from ..sources.canonicalize import canonicalize_url
from ..sources.extractor import extract_article
from ..sources.homepage import fetch_homepage
from ..sources.robots import can_fetch
from ..sources.rss import fetch_rss
from ..sources.source_config import load_sources
from ..topics.comparison import compare_sources
from ..topics.grouper import group_articles_into_topics
from ..topics.lifecycle import determine_lifecycle, severity_label
from ..utils.hashing import content_hash
from ..utils.time import utcnow_iso, is_within_hours

logger = logging.getLogger("news_agent.jobs.pipeline")


def _collect_source(source, user_agent):
    """Collect candidates from a single source (thread-safe)."""
    candidates = []
    try:
        if source.rss_url:
            candidates = fetch_rss(
                source.rss_url, source.name, user_agent,
                max_links=source.max_links,
            )
        if not candidates:
            candidates = fetch_homepage(
                source.homepage_url, source.name, user_agent,
                max_links=source.max_links,
                deny_patterns=source.deny_patterns,
                prefer_patterns=source.prefer_patterns,
            )
        return source, candidates, None
    except Exception as e:
        return source, [], e


def _extract_one(url, user_agent, domain_times, domain_lock):
    """Fetch and parse one article (thread-safe with per-domain rate limiting)."""
    domain = urlparse(url).netloc
    with domain_lock:
        last = domain_times.get(domain, 0)
        wait = max(0, 0.5 - (time.time() - last))
        if wait > 0:
            time.sleep(wait)
        domain_times[domain] = time.time()
    return extract_article(url, user_agent)


def _parse_published(published_str):
    """Try to parse a published date string into a timezone-aware datetime."""
    if not published_str:
        return None
    try:
        pub_str = published_str.replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(pub_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        return pub_dt
    except Exception:
        return None


def run_pipeline(config: dict) -> RunManifest:
    """Execute a single pipeline run."""
    run_id = str(uuid.uuid4())[:8]
    started_at = utcnow_iso()

    manifest = RunManifest(
        run_id=run_id,
        started_at=datetime.now(timezone.utc),
        status="running",
    )

    # Initialize
    db_path = config["paths"]["database"]
    conn = get_connection(db_path)
    init_schema(conn)

    # Repos
    article_repo = ArticleRepo(conn)
    text_repo = ArticleTextRepo(conn)
    score_repo = ScoreRepo(conn)
    claim_repo = ClaimRepo(conn)
    topic_repo = TopicRepo(conn)
    run_repo = RunRepo(conn)
    health_repo = SourceHealthRepo(conn)
    alert_repo = AlertRepo(conn)
    cache_repo = CacheRepo(conn)
    debug_repo = DebugRepo(conn)
    trusted_repo = TrustedSourceRepo(conn)

    run_repo.insert(run_id, started_at)

    # Sync trusted sources from config
    for ts in config.get("trusted_sources", []):
        trusted_repo.upsert(ts["name"], ts.get("category", ""),
                            ts.get("urls", []), ts.get("notes", ""))

    # Ensure directories
    for key in ("reports", "logs", "debug"):
        Path(config["paths"][key]).mkdir(parents=True, exist_ok=True)

    # Check Ollama
    client = OllamaClient(
        base_url=config["model"]["base_url"],
        model=config["model"]["name"],
        timeout=config["model"].get("timeout", 120),
        max_retries=config["model"].get("max_retries", 3),
    )

    ollama_available = client.is_available() and client.model_exists()
    if not ollama_available:
        logger.error("Ollama/model unavailable. Run will be degraded.")
        manifest.degraded = True
        manifest.errors.append("Ollama or model unavailable")

    # Setup cache
    cache = None
    if ollama_available:
        cache = AnalysisCache(
            cache_repo, config["model"]["name"],
            config.get("prompt_versions", {}).get("classification", "v1"),
        )

    # Load sources
    sources = load_sources(config)
    enabled_sources = [s for s in sources if s.enabled]
    scraping_cfg = config.get("scraping", {})
    user_agent = scraping_cfg.get("user_agent", "NewsAgent/1.0")
    recency_hours = scraping_cfg.get("recency_window_hours", 2)
    respect_robots = scraping_cfg.get("respect_robots_txt", True)

    concurrency_cfg = config.get("concurrency", {})
    collection_workers = concurrency_cfg.get("collection_workers", 8)
    extraction_workers = concurrency_cfg.get("extraction_workers", 8)

    # ===== COLLECTION (parallel) =====
    all_candidates = []
    logger.info(f"Collecting from {len(enabled_sources)} sources ({collection_workers} workers)...")

    with ThreadPoolExecutor(max_workers=collection_workers) as pool:
        futures = {
            pool.submit(_collect_source, source, user_agent): source
            for source in enabled_sources
        }
        for future in as_completed(futures):
            source = futures[future]
            manifest.sources_checked += 1
            start_time = time.time()

            try:
                src, candidates, error = future.result()
                if error:
                    raise error
                elapsed_ms = int((time.time() - start_time) * 1000)
                health_repo.insert(run_id, src.name, "ok",
                                   len(candidates), "", elapsed_ms)
                all_candidates.extend(candidates)
            except Exception as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                health_repo.insert(run_id, source.name, "error",
                                   0, str(e)[:500], elapsed_ms)
                manifest.sources_failed += 1
                manifest.errors.append(f"Source {source.name}: {e}")
                logger.error(f"Source collection failed for {source.name}: {e}")

    # ===== DEDUP & FILTER =====
    seen_urls = set()
    filtered = []
    for c in all_candidates:
        canonical = canonicalize_url(c.url)

        # Skip duplicates
        if canonical in seen_urls:
            continue
        seen_urls.add(canonical)

        # Skip if already in DB
        if article_repo.exists_by_canonical_url(canonical):
            continue

        # Recency check (only for articles with publish date)
        if c.published:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=recency_hours)
            if c.published < cutoff:
                continue

        # Robots.txt check
        if respect_robots and not can_fetch(c.url, user_agent):
            continue

        filtered.append((c, canonical))

    logger.info(f"Filtered {len(all_candidates)} candidates -> {len(filtered)} new articles")

    # ===== OVERLAPPED EXTRACTION + SCORING =====
    scored_articles = []
    model_failures = 0
    articles_collected = 0
    domain_times = {}
    domain_lock = threading.Lock()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=recency_hours)

    logger.info(f"Starting extraction ({extraction_workers} workers) + scoring (overlapped)...")

    with ThreadPoolExecutor(max_workers=extraction_workers) as extract_pool:
        # Submit all extraction jobs
        future_to_candidate = {
            extract_pool.submit(
                _extract_one, candidate.url, user_agent, domain_times, domain_lock
            ): (candidate, canonical)
            for candidate, canonical in filtered
        }

        # Process results as they complete — score immediately on main thread
        for future in as_completed(future_to_candidate):
            candidate, canonical = future_to_candidate[future]

            try:
                article_data = future.result()
            except Exception as e:
                logger.warning(f"Extraction failed for {candidate.url}: {e}")
                continue

            title = article_data.get("title") or candidate.title or ""
            body = article_data.get("body_text", "")

            # Skip articles with very little content
            if article_data.get("word_count", 0) < 50 and not body:
                continue

            # Determine published date
            published = article_data.get("published") or (
                candidate.published.isoformat() if candidate.published else None
            )

            # Post-extraction recency check for homepage-scraped articles
            if published and not candidate.published:
                pub_dt = _parse_published(published)
                if pub_dt and pub_dt < cutoff:
                    logger.debug(f"Skipping stale article: {candidate.url}")
                    continue

            # Store in DB (main thread — SQLite safe)
            try:
                article_id = article_repo.insert(
                    url=candidate.url,
                    canonical_url=canonical,
                    source_name=candidate.source_name,
                    title=title,
                    author=article_data.get("author", ""),
                    published=published,
                    word_count=article_data.get("word_count", 0),
                    language="en",
                )
            except Exception as e:
                logger.warning(f"Failed to insert article {candidate.url}: {e}")
                continue

            c_hash = content_hash(body)
            text_repo.store(article_id, body, c_hash)
            articles_collected += 1

            art = {
                "id": article_id,
                "url": candidate.url,
                "canonical_url": canonical,
                "source_name": candidate.source_name,
                "title": title,
                "body_text": body,
                "content_hash": c_hash,
                "word_count": article_data.get("word_count", 0),
            }

            # === SCORE IMMEDIATELY (main thread, serial Ollama) ===
            source_cfg = next(
                (s for s in enabled_sources if s.name == art["source_name"]), None
            )
            orientation = source_cfg.orientation if source_cfg else "center"
            credibility = source_cfg.credibility_level if source_cfg else "medium"

            if ollama_available:
                scores = score_article(
                    client, cache,
                    url=art["url"],
                    source_name=art["source_name"],
                    orientation=orientation,
                    credibility_level=credibility,
                    title=art["title"],
                    body_text=art["body_text"],
                    content_hash_val=art["content_hash"],
                    config=config,
                )
                if scores is None:
                    model_failures += 1
                    scores = fallback_score(art["url"], art["title"],
                                            art["body_text"], config)
            else:
                scores = fallback_score(art["url"], art["title"],
                                        art["body_text"], config)

            scores.article_id = art["id"]

            # Store score
            score_repo.insert(
                article_id=art["id"],
                criteria=scores.criteria,
                final_score=scores.final_score,
                override_triggered=scores.override_triggered,
                override_reason=scores.override_reason,
                labels=scores.labels,
                confidence=scores.confidence,
                model_name=scores.model_name,
                prompt_version=scores.prompt_version,
                rationale=scores.rationale,
            )
            article_repo.update_score(art["id"], scores.final_score)

            # Threshold handling
            thresholds = config.get("thresholds", {})
            if scores.final_score < thresholds.get("ignore_below", 4.0):
                text_repo.delete(art["id"])
            elif scores.final_score >= thresholds.get("report_minimum", 6.0):
                expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
                text_repo.store(art["id"], art["body_text"], art["content_hash"], expires)
                article_repo.schedule_refetch(art["id"])
                scored_articles.append({**art, "final_score": scores.final_score, "scores": scores})
            else:
                expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                text_repo.store(art["id"], art["body_text"], art["content_hash"], expires)

            manifest.articles_scored += 1

    manifest.articles_collected = articles_collected
    manifest.model_failures = model_failures
    if model_failures > articles_collected * 0.5 and articles_collected:
        manifest.degraded = True
        manifest.errors.append(f"High model failure rate: {model_failures}/{articles_collected}")

    # ===== CLAIM EXTRACTION =====
    claim_threshold = config.get("thresholds", {}).get("claim_extraction_minimum", 7.0)
    for art in scored_articles:
        if art["final_score"] >= claim_threshold and ollama_available:
            claims = extract_claims(
                client, art["id"], art["url"],
                art["source_name"], art["title"], art["body_text"],
            )
            for claim in claims:
                claim_repo.insert(
                    article_id=art["id"],
                    claim_text=claim.claim_text,
                    source_url=claim.source_url,
                    source_name=claim.source_name,
                    category=claim.category,
                    target_entity=claim.target_entity,
                    status=claim.status,
                    confidence=claim.confidence,
                    citation_url=claim.citation_url,
                )

    # ===== TOPIC GROUPING =====
    reportable_articles = [a for a in scored_articles
                           if a["final_score"] >= config.get("thresholds", {}).get("report_minimum", 6.0)]

    topic_groups = []
    if reportable_articles and ollama_available:
        topic_groups = group_articles_into_topics(
            client,
            reportable_articles,
            score_repo,
            topic_repo,
        )
    elif reportable_articles:
        # Fallback: each article is its own topic
        for i, art in enumerate(reportable_articles):
            topic_groups.append({
                "topic_name": art["title"],
                "summary": art["title"],
                "article_indices": [i],
                "existing_topic_id": None,
                "lifecycle": "emerging",
                "labels": art.get("scores", {}).labels if "scores" in art else [],
            })

    # Persist topics
    report_topics = []
    for group in topic_groups:
        indices = group.get("article_indices", [])
        if not indices:
            continue

        group_articles = [reportable_articles[i] for i in indices
                          if i < len(reportable_articles)]
        if not group_articles:
            continue

        # Calculate topic score (max of articles)
        topic_score = max(a["final_score"] for a in group_articles)
        source_names = list(set(a["source_name"] for a in group_articles))
        labels = group.get("labels", [])

        existing_id = group.get("existing_topic_id")
        if existing_id:
            # Update existing topic
            existing = topic_repo.get_by_id(existing_id)
            if existing:
                prev_count = existing.get("article_count", 0)
                new_count = prev_count + len(group_articles)
                lifecycle = determine_lifecycle(
                    new_count, len(source_names),
                    existing.get("lifecycle"), prev_count,
                )
                all_sources = list(set(existing.get("source_names", []) + source_names))
                all_labels = list(set(existing.get("labels", []) + labels))
                topic_repo.update(
                    existing_id, group.get("summary", ""),
                    lifecycle, topic_score, all_labels, all_sources, new_count,
                )
                for art in group_articles:
                    topic_repo.link_article(existing_id, art["id"])
                topic_id = existing_id
            else:
                existing_id = None

        if not existing_id:
            lifecycle = determine_lifecycle(
                len(group_articles), len(source_names), None, 0,
            )
            topic_id = topic_repo.insert(
                name=group.get("topic_name", "Unnamed"),
                summary=group.get("summary", ""),
                lifecycle=lifecycle,
                final_score=topic_score,
                labels=labels,
                source_names=source_names,
            )
            for art in group_articles:
                topic_repo.link_article(topic_id, art["id"])

        # Source comparison
        source_comparison = {}
        if len(group_articles) >= 2 and ollama_available:
            source_comparison = compare_sources(
                client, group.get("topic_name", ""),
                group.get("summary", ""), group_articles,
            )

        # Get claims for this topic
        topic_claims = []
        for art in group_articles:
            topic_claims.extend(claim_repo.get_by_article(art["id"]))

        # Link claims
        for c in topic_claims:
            topic_repo.link_claim(topic_id, c["id"])

        report_topics.append({
            "id": topic_id,
            "name": group.get("topic_name", "Unnamed"),
            "summary": group.get("summary", ""),
            "lifecycle": lifecycle,
            "final_score": topic_score,
            "labels": labels,
            "source_names": source_names,
            "claims": topic_claims,
            "source_comparison": source_comparison,
            "article_count": len(group_articles),
            "why_it_matters": "",
            "recommended_response": "",
        })

    manifest.topics_found = len(report_topics)

    # ===== REPORT SECTIONS (why_it_matters, recommended_response) =====
    from ..analysis.prompts import SYSTEM_ANALYST, REPORT_SECTION_PROMPT

    for topic in report_topics:
        if topic["final_score"] >= 7.0 and ollama_available:
            prompt = REPORT_SECTION_PROMPT.format(
                topic_name=topic["name"],
                severity=severity_label(topic["final_score"]),
                lifecycle=topic["lifecycle"],
                final_score=topic["final_score"],
                labels=", ".join(topic["labels"]),
                summary=topic["summary"],
                source_comparison=topic.get("source_comparison", {}).get("comparison_summary", ""),
                claims_json=json.dumps(topic["claims"][:5], default=str, indent=2),
            )
            result = client.generate_json(prompt, system=SYSTEM_ANALYST, temperature=0.3)
            if result and isinstance(result, dict):
                topic["why_it_matters"] = result.get("why_it_matters", "")
                topic["recommended_response"] = result.get("recommended_response", "")

    # ===== ALERTS =====
    alert_threshold = config.get("thresholds", {}).get("terminal_alert", 9.0)
    alerts = []
    for topic in report_topics:
        should_alert = topic["final_score"] >= alert_threshold

        # Check override triggers in scored articles
        if not should_alert:
            for art in scored_articles:
                if art.get("scores") and art["scores"].override_triggered:
                    should_alert = True
                    break

        if should_alert:
            alert_data = {
                "topic_name": topic["name"],
                "headline": topic["name"],
                "risk_score": topic["final_score"],
                "source_count": len(topic["source_names"]),
                "primary_sources": topic["source_names"][:3],
                "urls": [a["url"] for a in reportable_articles
                         if a["source_name"] in topic["source_names"]][:3],
                "reason": f"Score {topic['final_score']:.1f} | {', '.join(topic['labels'][:2])}",
            }
            alerts.append(alert_data)
            alert_repo.insert(run_id, **alert_data)

    manifest.alerts_raised = len(alerts)
    manifest.articles_reported = len(reportable_articles)

    # ===== DETECT CHANGES SINCE LAST RUN =====
    changes = []
    last_run = run_repo.get_latest()
    if last_run and last_run.get("id") != run_id:
        if manifest.articles_collected > 0:
            changes.append(f"{manifest.articles_collected} new articles collected")
        if report_topics:
            changes.append(f"{len(report_topics)} topics in report")
        if alerts:
            changes.append(f"{len(alerts)} new alerts raised")

    # ===== RENDER REPORT =====
    source_health = health_repo.get_by_run(run_id)
    report_path = render_report(
        run_id=run_id,
        status="degraded" if manifest.degraded else "healthy",
        topics=report_topics,
        alerts=alerts,
        changes=changes,
        source_health=source_health,
        output_dir=config["paths"]["reports"],
    )

    # ===== FINISH =====
    status = "degraded" if manifest.degraded else "healthy"
    manifest.status = status
    manifest.finished_at = datetime.now(timezone.utc)

    run_repo.finish(
        run_id, status,
        sources_checked=manifest.sources_checked,
        sources_failed=manifest.sources_failed,
        articles_collected=manifest.articles_collected,
        articles_scored=manifest.articles_scored,
        articles_reported=manifest.articles_reported,
        topics_found=manifest.topics_found,
        alerts_raised=manifest.alerts_raised,
        model_failures=manifest.model_failures,
        degraded=manifest.degraded,
        errors=manifest.errors,
    )

    # Terminal output
    print_alerts(alerts)
    print_run_summary({
        "status": status,
        "sources_checked": manifest.sources_checked,
        "sources_failed": manifest.sources_failed,
        "articles_collected": manifest.articles_collected,
        "articles_scored": manifest.articles_scored,
        "topics_found": manifest.topics_found,
        "alerts_raised": manifest.alerts_raised,
        "model_failures": manifest.model_failures,
    })
    print(f"  Report: {report_path}")

    conn.close()
    return manifest
