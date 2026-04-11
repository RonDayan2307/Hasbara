from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from utils import clean_whitespace, project_root

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class RuntimeSettings:
    path: Path
    os_type: str
    local_ai_model: str
    ollama_url: str
    ollama_timeout_seconds: int
    ollama_stream: bool
    review_mode: str
    report_mode: str
    review_batch_size: int
    max_review_stories: int
    max_article_paragraphs: int
    min_body_chars: int
    max_body_chars: int
    review_num_predict_per_story: int
    report_num_predict: int
    num_ctx: int
    progress_log_seconds: int
    topic_window_days: int
    topic_match_threshold: float
    criteria_path: Path
    review_worthy_min_score: int
    review_average_min_score: int
    priority_high_min_score: int
    priority_breaking_min_score: int
    source_config_path: Path
    source_rules_path: Path
    data_dir: Path
    debug_dir: Path
    desktop_dir: Path | None
    output_dir: Path | None
    report_subdir: str
    user_agent: str

    @property
    def topics_path(self) -> Path:
        return self.data_dir / "topics.json"

    @property
    def articles_dir(self) -> Path:
        return self.data_dir / "articles"

    @property
    def reviews_dir(self) -> Path:
        return self.data_dir / "reviews"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def review_cache_path(self) -> Path:
        return self.data_dir / "review_cache.json"

    def default_desktop_dir(self) -> Path:
        if self.desktop_dir:
            return self.desktop_dir

        os_type = self.os_type.lower()
        if os_type == "windows":
            userprofile = os.getenv("USERPROFILE")
            if userprofile:
                return Path(userprofile).expanduser() / "Desktop"
        return Path.home() / "Desktop"

    def report_output_dir(self) -> Path:
        if self.output_dir:
            return self.output_dir

        workspace_reports = project_root().parent / "reports"
        if workspace_reports.parent.exists():
            return workspace_reports

        return project_root() / "reports"


def load_runtime_settings(path: str | Path | None = None) -> RuntimeSettings:
    settings_path = Path(path) if path else _default_settings_path()
    if not settings_path.is_absolute():
        settings_path = project_root() / settings_path

    parsed = _parse_settings_file(settings_path)
    return RuntimeSettings(
        path=settings_path,
        os_type=_string_setting(parsed, "os_type", "macos").lower(),
        local_ai_model=_string_setting(parsed, "local_ai_model", "gemma4:e4b"),
        ollama_url=_string_setting(parsed, "ollama_url", "http://localhost:11434/api/chat"),
        ollama_timeout_seconds=_int_setting(parsed, "ollama_timeout_seconds", 600),
        ollama_stream=_bool_setting(parsed, "ollama_stream", False),
        review_mode=_string_setting(parsed, "review_mode", "batch").lower(),
        report_mode=_string_setting(parsed, "report_mode", "local").lower(),
        review_batch_size=max(1, _int_setting(parsed, "review_batch_size", 5)),
        max_review_stories=_int_setting(parsed, "max_review_stories", 5),
        max_article_paragraphs=max(1, _int_setting(parsed, "max_article_paragraphs", 4)),
        min_body_chars=max(40, _int_setting(parsed, "min_body_chars", 120)),
        max_body_chars=max(400, _int_setting(parsed, "max_body_chars", 900)),
        review_num_predict_per_story=max(60, _int_setting(parsed, "review_num_predict_per_story", 80)),
        report_num_predict=max(120, _int_setting(parsed, "report_num_predict", 420)),
        num_ctx=max(1024, _int_setting(parsed, "num_ctx", 2048)),
        progress_log_seconds=max(5, _int_setting(parsed, "progress_log_seconds", 15)),
        topic_window_days=max(1, _int_setting(parsed, "topic_window_days", 14)),
        topic_match_threshold=_float_setting(parsed, "topic_match_threshold", 0.52),
        criteria_path=_path_setting(parsed, "criteria_path", "config/review_criteria.txt"),
        review_worthy_min_score=max(1, min(10, _int_setting(parsed, "review_worthy_min_score", 7))),
        review_average_min_score=max(1, min(10, _int_setting(parsed, "review_average_min_score", 6))),
        priority_high_min_score=max(1, min(10, _int_setting(parsed, "priority_high_min_score", 8))),
        priority_breaking_min_score=max(1, min(10, _int_setting(parsed, "priority_breaking_min_score", 9))),
        source_config_path=_path_setting(parsed, "source_config_path", "config/source_sites.txt"),
        source_rules_path=_path_setting(parsed, "source_rules_path", "config/source_rules.txt"),
        data_dir=_path_setting(parsed, "data_dir", "data"),
        debug_dir=_path_setting(parsed, "debug_dir", "data/debug"),
        desktop_dir=_optional_path_setting(parsed, "desktop_dir"),
        output_dir=_optional_path_setting(parsed, "output_dir"),
        report_subdir=_string_setting(parsed, "report_subdir", "reports"),
        user_agent=_string_setting(parsed, "user_agent", _DEFAULT_USER_AGENT),
    )


def _default_settings_path() -> Path:
    env_path = os.getenv("NEWS_RUNTIME_SETTINGS_PATH")
    if env_path:
        return Path(env_path)
    return project_root() / "config" / "runtime_settings.txt"


def _parse_settings_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"Runtime settings file not found: {path}")

    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue

        key = clean_whitespace(key).lower().replace(" ", "_")
        value = clean_whitespace(value)
        if value:
            parsed[key] = value

    return parsed


def _string_setting(parsed: dict[str, str], key: str, default: str) -> str:
    return clean_whitespace(parsed.get(key, default)) or default


def _int_setting(parsed: dict[str, str], key: str, default: int) -> int:
    try:
        return int(parsed.get(key, default))
    except (TypeError, ValueError):
        return default


def _float_setting(parsed: dict[str, str], key: str, default: float) -> float:
    try:
        return float(parsed.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool_setting(parsed: dict[str, str], key: str, default: bool) -> bool:
    value = parsed.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _path_setting(parsed: dict[str, str], key: str, default: str) -> Path:
    value = parsed.get(key, default)
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root() / path


def _optional_path_setting(parsed: dict[str, str], key: str) -> Path | None:
    value = parsed.get(key)
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return project_root() / path
