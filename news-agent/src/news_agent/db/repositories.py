"""Repository classes for database operations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


class ArticleRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def exists_by_canonical_url(self, canonical_url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM articles WHERE canonical_url = ?", (canonical_url,)
        ).fetchone()
        return row is not None

    def insert(self, url: str, canonical_url: str, source_name: str,
               title: str, author: str, published: str | None,
               word_count: int, language: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO articles (url, canonical_url, source_name, title, author,
               published, word_count, language)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (url, canonical_url, source_name, title, author,
             published, word_count, language),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_score(self, article_id: int, final_score: float) -> None:
        self.conn.execute(
            "UPDATE articles SET final_score = ? WHERE id = ?",
            (final_score, article_id),
        )
        self.conn.commit()

    def schedule_refetch(self, article_id: int, days: int = 7) -> None:
        refetch_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        self.conn.execute(
            "UPDATE articles SET refetch_at = ? WHERE id = ?",
            (refetch_at, article_id),
        )
        self.conn.commit()

    def get_pending_refetches(self) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE refetch_at IS NOT NULL AND refetch_at <= ? AND refetched = 0",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_by_source(self, source_name: str, hours: int = 2) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE source_name = ? AND discovered_at >= ?",
            (source_name, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scored_articles(self, min_score: float) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE final_score IS NOT NULL AND final_score >= ?",
            (min_score,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, article_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return dict(row) if row else None


class ArticleTextRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def store(self, article_id: int, body_text: str, content_hash: str,
              expires_at: str | None = None) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO article_texts (article_id, body_text, content_hash, expires_at)
               VALUES (?, ?, ?, ?)""",
            (article_id, body_text, content_hash, expires_at),
        )
        self.conn.commit()

    def get(self, article_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT body_text FROM article_texts WHERE article_id = ?", (article_id,)
        ).fetchone()
        return row[0] if row else None

    def delete(self, article_id: int) -> None:
        self.conn.execute("DELETE FROM article_texts WHERE article_id = ?", (article_id,))
        self.conn.commit()

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "DELETE FROM article_texts WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount


class ScoreRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, article_id: int, criteria: dict, final_score: float,
               override_triggered: bool, override_reason: str,
               labels: list[str], confidence: float,
               model_name: str, prompt_version: str, rationale: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO article_scores
               (article_id, criteria_json, final_score, override_triggered, override_reason,
                labels_json, confidence, model_name, prompt_version, rationale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (article_id, json.dumps(criteria), final_score,
             1 if override_triggered else 0, override_reason,
             json.dumps(labels), confidence, model_name, prompt_version, rationale),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_by_article(self, article_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM article_scores WHERE article_id = ? ORDER BY scored_at DESC LIMIT 1",
            (article_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["criteria"] = json.loads(d["criteria_json"])
        d["labels"] = json.loads(d["labels_json"])
        return d


class ClaimRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, article_id: int, claim_text: str, source_url: str,
               source_name: str, category: str, target_entity: str,
               status: str, confidence: float, citation_url: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO claims
               (article_id, claim_text, source_url, source_name, category,
                target_entity, status, confidence, citation_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (article_id, claim_text, source_url, source_name, category,
             target_entity, status, confidence, citation_url),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_by_article(self, article_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM claims WHERE article_id = ?", (article_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_topic(self, topic_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.* FROM claims c
               JOIN topic_claims tc ON c.id = tc.claim_id
               WHERE tc.topic_id = ?""",
            (topic_id,),
        ).fetchall()
        return [dict(r) for r in rows]


class TopicRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, name: str, summary: str, lifecycle: str,
               final_score: float, labels: list[str],
               source_names: list[str]) -> int:
        cur = self.conn.execute(
            """INSERT INTO topics (name, summary, lifecycle, final_score,
               labels_json, source_names_json, article_count)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (name, summary, lifecycle, final_score,
             json.dumps(labels), json.dumps(source_names)),
        )
        self.conn.commit()
        return cur.lastrowid

    def update(self, topic_id: int, summary: str, lifecycle: str,
               final_score: float, labels: list[str],
               source_names: list[str], article_count: int) -> None:
        self.conn.execute(
            """UPDATE topics SET summary = ?, lifecycle = ?, final_score = ?,
               labels_json = ?, source_names_json = ?, article_count = ?,
               last_updated = datetime('now')
               WHERE id = ?""",
            (summary, lifecycle, final_score,
             json.dumps(labels), json.dumps(source_names),
             article_count, topic_id),
        )
        self.conn.commit()

    def link_article(self, topic_id: int, article_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO topic_articles (topic_id, article_id) VALUES (?, ?)",
            (topic_id, article_id),
        )
        self.conn.commit()

    def link_claim(self, topic_id: int, claim_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO topic_claims (topic_id, claim_id) VALUES (?, ?)",
            (topic_id, claim_id),
        )
        self.conn.commit()

    def get_active(self, days: int = 14) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM topics WHERE last_updated >= ?", (cutoff,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["labels"] = json.loads(d["labels_json"])
            d["source_names"] = json.loads(d["source_names_json"])
            result.append(d)
        return result

    def get_topic_articles(self, topic_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT a.* FROM articles a
               JOIN topic_articles ta ON a.id = ta.article_id
               WHERE ta.topic_id = ?""",
            (topic_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, topic_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["labels"] = json.loads(d["labels_json"])
        d["source_names"] = json.loads(d["source_names_json"])
        return d


class RunRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, run_id: str, started_at: str) -> None:
        self.conn.execute(
            "INSERT INTO runs (id, started_at) VALUES (?, ?)",
            (run_id, started_at),
        )
        self.conn.commit()

    def finish(self, run_id: str, status: str, **kwargs) -> None:
        finished = datetime.now(timezone.utc).isoformat()
        fields = ["finished_at = ?", "status = ?"]
        values = [finished, status]
        for key, val in kwargs.items():
            if key == "errors":
                fields.append("errors_json = ?")
                values.append(json.dumps(val))
            elif key == "degraded":
                fields.append("degraded = ?")
                values.append(1 if val else 0)
            else:
                fields.append(f"{key} = ?")
                values.append(val)
        values.append(run_id)
        self.conn.execute(
            f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values
        )
        self.conn.commit()

    def get_latest(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


class SourceHealthRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, run_id: str, source_name: str, status: str,
               articles_found: int, error_message: str,
               response_time_ms: int) -> None:
        self.conn.execute(
            """INSERT INTO source_health
               (run_id, source_name, status, articles_found, error_message, response_time_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, source_name, status, articles_found,
             error_message, response_time_ms),
        )
        self.conn.commit()

    def get_by_run(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM source_health WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


class AlertRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, run_id: str, topic_name: str, headline: str,
               risk_score: float, source_count: int,
               primary_sources: list[str], urls: list[str],
               reason: str) -> int:
        cur = self.conn.execute(
            """INSERT INTO alerts
               (run_id, topic_name, headline, risk_score, source_count,
                primary_sources_json, urls_json, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, topic_name, headline, risk_score, source_count,
             json.dumps(primary_sources), json.dumps(urls), reason),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_by_run(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM alerts WHERE run_id = ?", (run_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["primary_sources"] = json.loads(d["primary_sources_json"])
            d["urls"] = json.loads(d["urls_json"])
            result.append(d)
        return result


class CacheRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, cache_key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT response_json FROM model_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def set(self, cache_key: str, response: dict,
            model_name: str, prompt_version: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO model_cache
               (cache_key, response_json, model_name, prompt_version)
               VALUES (?, ?, ?, ?)""",
            (cache_key, json.dumps(response), model_name, prompt_version),
        )
        self.conn.commit()


class DebugRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log(self, run_id: str | None, event_type: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO debug_events (run_id, event_type, payload) VALUES (?, ?, ?)",
            (run_id, event_type, json.dumps(payload)),
        )
        self.conn.commit()

    def cleanup(self, days: int = 14) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "DELETE FROM debug_events WHERE created_at < ?", (cutoff,)
        )
        self.conn.commit()
        return cur.rowcount


class TrustedSourceRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, name: str, category: str, urls: list[str], notes: str) -> None:
        self.conn.execute(
            """INSERT INTO trusted_sources (name, category, urls_json, notes)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET category=?, urls_json=?, notes=?""",
            (name, category, json.dumps(urls), notes,
             category, json.dumps(urls), notes),
        )
        self.conn.commit()

    def get_all(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM trusted_sources").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["urls"] = json.loads(d["urls_json"])
            result.append(d)
        return result
