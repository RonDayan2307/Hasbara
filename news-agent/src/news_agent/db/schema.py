"""Database schema creation and migrations."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    homepage_url TEXT NOT NULL,
    rss_url TEXT,
    enabled INTEGER DEFAULT 1,
    language TEXT DEFAULT 'en',
    region TEXT DEFAULT 'international',
    orientation TEXT DEFAULT 'center',
    credibility_level TEXT DEFAULT 'medium',
    priority INTEGER DEFAULT 2,
    max_links INTEGER DEFAULT 100,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    title TEXT DEFAULT '',
    author TEXT DEFAULT '',
    published TEXT,
    discovered_at TEXT DEFAULT (datetime('now')),
    word_count INTEGER DEFAULT 0,
    language TEXT DEFAULT 'en',
    final_score REAL,
    refetch_at TEXT,
    refetched INTEGER DEFAULT 0,
    UNIQUE(canonical_url)
);

CREATE TABLE IF NOT EXISTS article_texts (
    article_id INTEGER PRIMARY KEY REFERENCES articles(id),
    body_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stored_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS article_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    criteria_json TEXT NOT NULL,
    final_score REAL NOT NULL,
    override_triggered INTEGER DEFAULT 0,
    override_reason TEXT DEFAULT '',
    labels_json TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.0,
    model_name TEXT DEFAULT '',
    prompt_version TEXT DEFAULT '',
    rationale TEXT DEFAULT '',
    scored_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    topic_id INTEGER REFERENCES topics(id),
    claim_text TEXT NOT NULL,
    source_url TEXT DEFAULT '',
    source_name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    target_entity TEXT DEFAULT '',
    status TEXT DEFAULT 'needs_human_verification',
    confidence REAL DEFAULT 0.0,
    citation_url TEXT DEFAULT '',
    extracted_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    summary TEXT DEFAULT '',
    lifecycle TEXT DEFAULT 'emerging',
    final_score REAL DEFAULT 0.0,
    labels_json TEXT DEFAULT '[]',
    source_names_json TEXT DEFAULT '[]',
    first_seen TEXT DEFAULT (datetime('now')),
    last_updated TEXT DEFAULT (datetime('now')),
    article_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS topic_articles (
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    article_id INTEGER NOT NULL REFERENCES articles(id),
    PRIMARY KEY (topic_id, article_id)
);

CREATE TABLE IF NOT EXISTS topic_claims (
    topic_id INTEGER NOT NULL REFERENCES topics(id),
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    PRIMARY KEY (topic_id, claim_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    sources_checked INTEGER DEFAULT 0,
    sources_failed INTEGER DEFAULT 0,
    articles_collected INTEGER DEFAULT 0,
    articles_scored INTEGER DEFAULT 0,
    articles_reported INTEGER DEFAULT 0,
    topics_found INTEGER DEFAULT 0,
    alerts_raised INTEGER DEFAULT 0,
    model_failures INTEGER DEFAULT 0,
    degraded INTEGER DEFAULT 0,
    errors_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS source_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    source_name TEXT NOT NULL,
    status TEXT DEFAULT 'ok',
    articles_found INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    response_time_ms INTEGER DEFAULT 0,
    checked_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id),
    topic_name TEXT DEFAULT '',
    headline TEXT DEFAULT '',
    risk_score REAL DEFAULT 0.0,
    source_count INTEGER DEFAULT 0,
    primary_sources_json TEXT DEFAULT '[]',
    urls_json TEXT DEFAULT '[]',
    reason TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS model_cache (
    cache_key TEXT PRIMARY KEY,
    response_json TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS debug_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trusted_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    category TEXT DEFAULT '',
    urls_json TEXT DEFAULT '[]',
    notes TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_articles_canonical ON articles(canonical_url);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_name);
CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_at);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(final_score);
CREATE INDEX IF NOT EXISTS idx_claims_article ON claims(article_id);
CREATE INDEX IF NOT EXISTS idx_claims_topic ON claims(topic_id);
CREATE INDEX IF NOT EXISTS idx_topic_articles_topic ON topic_articles(topic_id);
CREATE INDEX IF NOT EXISTS idx_topic_articles_article ON topic_articles(article_id);
CREATE INDEX IF NOT EXISTS idx_source_health_run ON source_health(run_id);
CREATE INDEX IF NOT EXISTS idx_alerts_run ON alerts(run_id);
CREATE INDEX IF NOT EXISTS idx_debug_created ON debug_events(created_at);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize the database schema."""
    conn.executescript(SCHEMA_SQL)
    # Set version
    existing = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if existing is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Get current schema version."""
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
