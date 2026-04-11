import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from analyzer import LocalAiAnalyzer
from memory import TopicMemory
from ollama_client import OllamaClient
from review_cache import ReviewCache
from scraper import SkippedArticle, _candidate_skip_reason, iter_stories, load_sources
from settings import load_runtime_settings
from telemetry import IngestionTelemetry
from utils import shorten_for_display
from writer import build_run_manifest
from report_renderer import render_report_from_reviews


def _make_settings(tempdir: Path):
    criteria_path = tempdir / "criteria.txt"
    criteria_path.write_text(
        "\n".join(
            [
                "israel_political_relevance | politically relevant to Israel, Israeli policy, or Israeli interests",
                "antisemitic_content | antisemitic, targeting Jewish people as a group, or promoting hatred toward Jews",
                "anti_zionist_content | anti-Zionist, denying Israel's right to exist, or framing Zionism as inherently illegitimate",
                "misinformation_risk | misleading, false, manipulative, or clearly unverified claims",
                "virality | viral, highly shareable, or likely to spread quickly across social media",
            ]
        ),
        encoding="utf-8",
    )
    source_sites = tempdir / "sources.txt"
    source_sites.write_text(
        "\n".join(
            [
                "Source A | https://example.com/a | 2 | English | center | 5",
                "Source B | https://example.com/b | 2 | English | wire | 4",
            ]
        ),
        encoding="utf-8",
    )
    source_rules = tempdir / "rules.txt"
    source_rules.write_text("", encoding="utf-8")
    runtime_path = tempdir / "runtime_settings.txt"
    runtime_path.write_text(
        "\n".join(
            [
                "os_type=macos",
                "output_dir=" + str(tempdir / "reports"),
                "local_ai_model=gemma4:e4b",
                "ollama_url=http://localhost:11434/api/chat",
                "ollama_timeout_seconds=60",
                "ollama_stream=false",
                "report_mode=local",
                "max_review_stories=4",
                "max_article_paragraphs=4",
                "min_body_chars=80",
                "max_body_chars=600",
                "review_num_predict_per_story=120",
                "report_num_predict=240",
                "num_ctx=2048",
                "progress_log_seconds=15",
                "criteria_path=" + str(criteria_path),
                "review_worthy_min_score=7",
                "review_average_min_score=6",
                "priority_high_min_score=8",
                "priority_breaking_min_score=9",
                "min_usable_review_ratio=0.8",
                "topic_window_days=14",
                "topic_match_threshold=0.5",
                "source_config_path=" + str(source_sites),
                "source_rules_path=" + str(source_rules),
                "data_dir=" + str(tempdir / "data"),
                "debug_dir=" + str(tempdir / "data" / "debug"),
            ]
        ),
        encoding="utf-8",
    )
    return load_runtime_settings(runtime_path)


def _story(story_id: str, *, source: str = "Source A", title: str = "Israel and Iran talks continue"):
    return {
        "id": story_id,
        "source": source,
        "source_language": "English",
        "source_orientation": "center",
        "source_priority": 5,
        "title": title,
        "url": f"https://example.com/{story_id}",
        "body": (
            "Israel and Iran held talks after a ceasefire. "
            "Officials said the diplomatic process could continue if attacks stop."
        ),
        "description": "test description",
        "published_at": "2026-04-11T01:00:00+00:00",
        "collected_at": "2026-04-11T01:05:00+00:00",
        "metrics": {"views": None, "likes": None, "shares": None, "comments": None},
    }


class SettingsAndCacheTests(unittest.TestCase):
    def test_display_shortener_avoids_mid_word_cutoff(self):
        title = "Lebanon thought there was a ceasefire then Israel unleashed deadly strikes overnight"
        shortened = shorten_for_display(title, max_length=48)
        self.assertLessEqual(len(shortened), 48)
        self.assertTrue(shortened.endswith("..."))
        self.assertNotIn("stri...", shortened)

    def test_settings_removed_dead_knobs_and_loads_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            self.assertFalse(hasattr(settings, "review_mode"))
            self.assertFalse(hasattr(settings, "review_batch_size"))
            self.assertEqual(settings.local_ai_model, "gemma4:e4b")
            self.assertEqual(settings.min_usable_review_ratio, 0.8)

    def test_settings_fail_cleanly_on_missing_required_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tempdir = Path(tmp)
            missing = tempdir / "missing.txt"
            runtime_path = tempdir / "runtime_settings.txt"
            runtime_path.write_text(
                "\n".join(
                    [
                        "criteria_path=" + str(missing),
                        "source_config_path=" + str(missing),
                        "source_rules_path=" + str(missing),
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(FileNotFoundError):
                load_runtime_settings(runtime_path)

    def test_review_cache_namespaces_and_labels_cached_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review_cache.json"
            cache = ReviewCache.load(path, "ns-1")
            cache.put("story-1", {"story_id": "story-1", "review_method": "model", "confidence": "high"})
            cache.save()

            cache_same = ReviewCache.load(path, "ns-1")
            cached = cache_same.get("story-1")
            self.assertIsNotNone(cached)
            self.assertEqual(cached["review_method"], "cached")
            self.assertEqual(cached["original_review_method"], "model")

            cache_other = ReviewCache.load(path, "ns-2")
            self.assertIsNone(cache_other.get("story-1"))


class HealthAndReviewTests(unittest.TestCase):
    def test_health_check_requires_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            client = OllamaClient(settings)

            client.chat = MethodType(lambda self, *args, **kwargs: "", client)
            self.assertFalse(client.health_check())

            client.chat = MethodType(lambda self, *args, **kwargs: "not json", client)
            self.assertFalse(client.health_check())

            client.chat = MethodType(lambda self, *args, **kwargs: '{"ok": true}', client)
            self.assertTrue(client.health_check())

    def test_invalid_retry_creates_debug_and_recovers_or_falls_back_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)
            responses = ["", '{"story_id":"one","topic":"Test","summary":"x","why":"y","confidence":"high","claims":["a"],"scores":{"israel_political_relevance":7']

            def fake_chat(_messages, *, num_predict, json_format=False):
                return responses.pop(0)

            analyzer.client.chat = fake_chat
            review = analyzer.review_story(_story("one", title="Routine weather story"))
            self.assertIn(review["review_method"], {"model", "heuristic_fallback"})

            debug_dir = settings.debug_dir / "ollama"
            self.assertTrue(any(debug_dir.iterdir()))

    def test_valid_structured_review_normalizes_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)

            def fake_chat(_messages, *, num_predict, json_format=False):
                return json.dumps(
                    {
                        "story_id": "alpha",
                        "topic": "Iran ceasefire talks",
                        "summary": "Israel and Iran continued negotiations after a ceasefire.",
                        "why": "The article directly concerns Israeli diplomatic and security interests.",
                        "confidence": "high",
                        "claims": ["Talks continued after the ceasefire."],
                        "scores": {
                            "israel_political_relevance": 9,
                            "antisemitic_content": 1,
                            "anti_zionist_content": 1,
                            "misinformation_risk": 2,
                            "virality": 3,
                        },
                    }
                )

            analyzer.client.chat = fake_chat
            review = analyzer.review_story(_story("alpha"))
            self.assertEqual(review["review_method"], "model")
            self.assertEqual(review["review_quality"], "high_confidence")
            self.assertTrue(review["worth_reviewing"])
            self.assertEqual(review["priority"], "breaking")

    def test_truncated_primary_json_is_salvaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)

            def fake_chat(_messages, *, num_predict, json_format=False):
                return (
                    '{"i":"alpha","t":"Iran ceasefire talks","s":"Israel and Iran continued talks after the ceasefire.",'
                    '"w":"Direct Israeli diplomatic relevance.","c":"high","l":["Talks continued after the ceasefire."],'
                    '"g":{"ipr":9,"ac":1,"azc":1,"mr":2'
                )

            analyzer.client.chat = fake_chat
            review = analyzer.review_story(_story("alpha"))
            self.assertEqual(review["review_method"], "model")
            self.assertNotEqual(review["review_quality"], "fallback")
            self.assertTrue(review["worth_reviewing"])
            self.assertEqual(review["priority"], "breaking")

    def test_compact_line_retry_parses_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)
            responses = [
                "not valid json",
                "\n".join(
                    [
                        "i=beta",
                        "t=Iran ceasefire talks",
                        "s=Israel and Iran continued talks after the ceasefire.",
                        "w=Direct Israeli diplomatic relevance.",
                        "c=high",
                        "l=Talks continued after the ceasefire",
                        "g=ipr:9,ac:1,azc:1,mr:2,v:3",
                    ]
                ),
            ]

            def fake_chat(_messages, *, num_predict, json_format=False):
                return responses.pop(0)

            analyzer.client.chat = fake_chat
            review = analyzer.review_story(_story("beta"))
            self.assertEqual(review["review_method"], "model")
            self.assertTrue(review["worth_reviewing"])
            self.assertEqual(review["priority"], "breaking")

    def test_repair_retry_requests_another_output_after_compact_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)
            calls = []
            responses = [
                "not valid json",
                "still malformed output without usable fields",
                "\n".join(
                    [
                        "i=gamma",
                        "t=Iran ceasefire talks",
                        "s=Israel and Iran continued talks after the ceasefire.",
                        "w=Direct Israeli diplomatic relevance.",
                        "c=high",
                        "l=Talks continued after the ceasefire",
                        "g=ipr:9,ac:1,azc:1,mr:2,v:3",
                    ]
                ),
            ]

            def fake_chat(_messages, *, num_predict, json_format=False):
                calls.append({"num_predict": num_predict, "json_format": json_format})
                return responses.pop(0)

            analyzer.client.chat = fake_chat
            review = analyzer.review_story(_story("gamma"))
            self.assertEqual(review["review_method"], "model")
            self.assertTrue(review["worth_reviewing"])
            self.assertEqual(review["priority"], "breaking")
            self.assertEqual(len(calls), 3)


class ScraperTests(unittest.TestCase):
    def test_source_text_config_supports_fallbacks_and_warning_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.txt"
            path.write_text(
                "\n".join(
                    [
                        "Reuters World | https://www.reuters.com/world/ | 4 | English | wire_service | 4 | "
                        "fallback_homepages=https://www.reuters.com/world/middle-east/;https://www.reuters.com/world/europe/ | "
                        "warn_on_failure=false"
                    ]
                ),
                encoding="utf-8",
            )
            sources = load_sources(path)
            self.assertEqual(len(sources), 1)
            self.assertEqual(
                sources[0]["fallback_homepages"],
                [
                    "https://www.reuters.com/world/middle-east/",
                    "https://www.reuters.com/world/europe/",
                ],
            )
            self.assertFalse(sources[0]["warn_on_failure"])

    def test_source_rules_block_noise(self):
        source = {"deny_paths": ["/topic/"], "deny_titles": ["explainer"], "allow_paths": ["/news/"]}
        self.assertIn("blocked", _candidate_skip_reason(source, "https://example.com/topic/a", "Title"))
        self.assertIn("blocked", _candidate_skip_reason(source, "https://example.com/news/a", "Daily explainer"))
        self.assertIn("allow rule", _candidate_skip_reason(source, "https://example.com/sports/a", "Story"))

    def test_round_robin_spans_sources_and_source_failures_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            telemetry = IngestionTelemetry()

            def fake_links(source, _session, homepage_url=None):
                if source["name"] == "Source B":
                    return [
                        {"source": "Source B", "source_language": "English", "source_orientation": "wire", "source_priority": 4, "title": "Story B1", "url": "https://example.com/b1"},
                        {"source": "Source B", "source_language": "English", "source_orientation": "wire", "source_priority": 4, "title": "Story B2", "url": "https://example.com/b2"},
                    ]
                return [
                    {"source": "Source A", "source_language": "English", "source_orientation": "center", "source_priority": 5, "title": "Story A1", "url": "https://example.com/a1"},
                    {"source": "Source A", "source_language": "English", "source_orientation": "center", "source_priority": 5, "title": "Story A2", "url": "https://example.com/a2"},
                ]

            def fake_article(url, _session, _settings, *, source=None):
                return {"body": "Israel policy story body " * 10, "canonical_url": url, "published_at": None, "description": None, "metrics": {}}

            with mock.patch("scraper.extract_homepage_links", side_effect=fake_links), mock.patch(
                "scraper.extract_article",
                side_effect=fake_article,
            ):
                stories = list(iter_stories(settings, limit=4, telemetry=telemetry))

            self.assertEqual([story["source"] for story in stories], ["Source A", "Source B", "Source A", "Source B"])
            self.assertEqual(telemetry.source_failures, 0)

            telemetry = IngestionTelemetry()

            def fake_links_with_failure(source, _session, homepage_url=None):
                if source["name"] == "Source B":
                    raise RuntimeError("401 forbidden")
                return fake_links(source, _session)

            with mock.patch("scraper.extract_homepage_links", side_effect=fake_links_with_failure), mock.patch(
                "scraper.extract_article",
                side_effect=fake_article,
            ):
                stories = list(iter_stories(settings, limit=2, telemetry=telemetry))

            self.assertEqual(len(stories), 2)
            self.assertEqual(telemetry.source_failures, 1)

    def test_skipped_article_does_not_abort_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            telemetry = IngestionTelemetry()

            def fake_links(source, _session, homepage_url=None):
                return [
                    {"source": source["name"], "source_language": "English", "source_orientation": "center", "source_priority": int(source["priority"]), "title": f"{source['name']} Story 1", "url": f"https://example.com/{source['name']}/1"},
                    {"source": source["name"], "source_language": "English", "source_orientation": "center", "source_priority": int(source["priority"]), "title": f"{source['name']} Story 2", "url": f"https://example.com/{source['name']}/2"},
                ]

            def fake_article(url, _session, _settings, *, source=None):
                if url.endswith("/1"):
                    raise SkippedArticle("no article-like paragraphs extracted")
                return {"body": "Israel policy story body " * 10, "canonical_url": url, "published_at": None, "description": None, "metrics": {}}

            with mock.patch("scraper.extract_homepage_links", side_effect=fake_links), mock.patch(
                "scraper.extract_article",
                side_effect=fake_article,
            ):
                stories = list(iter_stories(settings, limit=2, telemetry=telemetry))

            self.assertEqual(len(stories), 2)
            self.assertGreaterEqual(telemetry.candidate_skips, 1)


class TopicAndReportTests(unittest.TestCase):
    def test_topic_memory_is_deterministic_for_new_and_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            memory = TopicMemory.load(settings)
            story_one = _story("one", source="Source A", title="Israel and Iran talks continue")
            story_two = _story("two", source="Source B", title="Israel and Iran talks continue in Doha")
            review = {
                "story_id": "one",
                "reviewed_at": "2026-04-11T01:05:00+00:00",
                "model": "gemma4:e4b",
                "review_method": "model",
                "review_quality": "high_confidence",
                "worth_reviewing": True,
                "priority": "high",
                "criteria_scores": [],
                "score_summary": {"max_score": 8.0, "average_score": 4.0},
                "source_language": "English",
                "political_orientation": "center",
                "mentions": ["Israel", "Iran"],
                "topic_hint": "Israel Iran talks",
                "summary": "Talks continued.",
                "claims_to_verify": [],
                "review_reason": "High relevance.",
                "confidence": "high",
                "prompt_version": "p",
                "normalization_version": "n",
                "cache_namespace": "c",
            }
            attachment_one = memory.attach(story_one, review)
            attachment_two = memory.attach(story_two, dict(review, story_id="two"))
            self.assertEqual(attachment_one["topic_status"], "new")
            self.assertEqual(attachment_two["topic_status"], "existing")

    def test_manifest_and_report_show_cached_and_multi_source_truthfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _make_settings(Path(tmp))
            analyzer = LocalAiAnalyzer(settings)
            criteria = analyzer.criteria
            reviewed_items = [
                {
                    "story": _story("one", source="Source A", title="Israel and Iran talks continue"),
                    "review": {
                        "story_id": "one",
                        "reviewed_at": "2026-04-11T01:05:00+00:00",
                        "model": "gemma4:e4b",
                        "review_method": "model",
                        "review_quality": "high_confidence",
                        "worth_reviewing": True,
                        "priority": "high",
                        "criteria_scores": [{"criterion": criteria[0].name, "material": criteria[0].material, "score": 9, "reason": "Model score."}],
                        "score_summary": {"max_score": 9.0, "average_score": 9.0},
                        "source_language": "English",
                        "political_orientation": "center",
                        "mentions": ["Israel", "Iran"],
                        "topic_hint": "Israel Iran talks",
                        "summary": "Talks continued after the ceasefire.",
                        "claims_to_verify": [],
                        "review_reason": "High relevance.",
                        "confidence": "high",
                        "prompt_version": analyzer.prompt_version,
                        "normalization_version": analyzer.normalization_version,
                        "cache_namespace": analyzer.cache_namespace,
                    },
                    "topic_status": "new",
                    "topic": {"name": "Israel Iran talks", "source_count": 2, "item_count": 2},
                    "cross_check": {},
                },
                {
                    "story": _story("two", source="Source B", title="Iran and Israel continue talks"),
                    "review": {
                        "story_id": "two",
                        "reviewed_at": "2026-04-11T01:06:00+00:00",
                        "model": "gemma4:e4b",
                        "review_method": "cached",
                        "review_quality": "high_confidence",
                        "worth_reviewing": True,
                        "priority": "high",
                        "criteria_scores": [{"criterion": criteria[0].name, "material": criteria[0].material, "score": 8, "reason": "Model score."}],
                        "score_summary": {"max_score": 8.0, "average_score": 8.0},
                        "source_language": "English",
                        "political_orientation": "wire",
                        "mentions": ["Israel", "Iran"],
                        "topic_hint": "Israel Iran talks",
                        "summary": "A second source also described the talks.",
                        "claims_to_verify": [],
                        "review_reason": "Corroborated by another source.",
                        "confidence": "high",
                        "prompt_version": analyzer.prompt_version,
                        "normalization_version": analyzer.normalization_version,
                        "cache_namespace": analyzer.cache_namespace,
                    },
                    "topic_status": "existing",
                    "topic": {"name": "Israel Iran talks", "source_count": 2, "item_count": 2},
                    "cross_check": {},
                },
                {
                    "story": _story("three", source="Source A", title="Sports round-up"),
                    "review": {
                        "story_id": "three",
                        "reviewed_at": "2026-04-11T01:07:00+00:00",
                        "model": "gemma4:e4b",
                        "review_method": "heuristic_fallback",
                        "review_quality": "fallback",
                        "worth_reviewing": False,
                        "priority": "ignore",
                        "criteria_scores": [{"criterion": criteria[0].name, "material": criteria[0].material, "score": 1, "reason": "Heuristic fallback for israel_political_relevance."}],
                        "score_summary": {"max_score": 1.0, "average_score": 1.0},
                        "source_language": "English",
                        "political_orientation": "center",
                        "mentions": [],
                        "topic_hint": "Sports round-up",
                        "summary": "A sports article.",
                        "claims_to_verify": [],
                        "review_reason": "Fallback was used.",
                        "confidence": "low",
                        "prompt_version": analyzer.prompt_version,
                        "normalization_version": analyzer.normalization_version,
                        "cache_namespace": analyzer.cache_namespace,
                    },
                    "topic_status": "excluded",
                    "topic": {},
                    "cross_check": {},
                },
            ]
            manifest = build_run_manifest(
                settings,
                [item["story"] for item in reviewed_items],
                reviewed_items,
                run_id="2026-04-11_05-00-00",
                source_health=[],
                cache_namespace=analyzer.cache_namespace,
                prompt_version=analyzer.prompt_version,
                normalization_version=analyzer.normalization_version,
            )
            self.assertEqual(manifest["counts"]["fresh_model_reviews"], 1)
            self.assertEqual(manifest["counts"]["cached_reviews"], 1)
            self.assertEqual(manifest["counts"]["heuristic_fallback_reviews"], 1)
            report = render_report_from_reviews(
                reviewed_items,
                criteria,
                run_manifest=manifest,
                model_name=settings.local_ai_model,
            )
            self.assertIn("corroborated multi-source topic", report)
            self.assertIn("1 cached", report)


if __name__ == "__main__":
    unittest.main()
