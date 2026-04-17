from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from contracts import CriterionScore
from criteria import ReviewCriterion
from utils import clean_whitespace, shorten_for_display

if TYPE_CHECKING:
    from ollama_client import OllamaClient
    from settings import RuntimeSettings
    from contracts import Story

log = logging.getLogger(__name__)

# Keyword sets used by the heuristic fallback for each known criterion.
# Any criterion not listed here falls back to words from its material text.
_HEURISTIC_KEYWORDS: dict[str, list[str]] = {
    "israel_political_relevance": [
        "israel", "israeli", "idf", "knesset", "netanyahu",
        "gaza", "west bank", "tel aviv", "jerusalem", "hezbollah",
        "hamas", "iran", "ceasefire", "hostage",
    ],
    "antisemitic_content": [
        "antisemit", "anti-semit", "jewish conspiracy", "jews control",
        "zionist plot", "hate jews", "jew-hating",
    ],
    "anti_zionist_content": [
        "anti-zionist", "anti zionist", "zionism is", "deny israel",
        "apartheid state", "settler coloni", "zionis",
    ],
    "misinformation_risk": [
        "fake news", "unverified", "claim", "alleged", "reportedly",
        "sources say", "anonymous", "false", "manipulat",
    ],
    "virality": [
        "breaking", "exclusive", "shocking", "viral", "trending",
        "millions", "outrage", "widely shared",
    ],
    "narrative_delegitimization": [
        "illegitimate", "ethnic cleansing", "genocide", "war crime",
        "occupation", "apartheid", "coloni", "delegitim",
    ],
    "source_credibility": [
        "propaganda", "state media", "disinformation", "state-controlled",
        "known disinfo",
    ],
}


def _heuristic_score(text: str, criterion_name: str, material: str) -> int:
    """Keyword-based score used as fallback when the model is unavailable."""
    keywords = _HEURISTIC_KEYWORDS.get(criterion_name) or [
        w for w in material.lower().split() if len(w) > 4
    ]
    hits = sum(1 for kw in keywords if kw in text)
    if hits == 0:
        return 2
    if hits == 1:
        return 4
    if hits == 2:
        return 6
    return 8


class CriterionSkill:
    """An independently callable scoring skill for a single review criterion.

    Each skill wraps one ReviewCriterion and can score any story against it
    using either a live Ollama call (via ``score()``) or a keyword heuristic
    (via ``heuristic()``).

    Skills are the unit of modularity: different platforms can load different
    subsets of skills and call them individually, without running the full
    batch-review pipeline in LocalAiAnalyzer.

    Usage (standalone per-criterion call)::

        skill = CriterionSkill(criterion)
        cs: CriterionScore = skill.score(story, client, settings)

    Usage (batch via LocalAiAnalyzer)::

        analyzer.skills   # list[CriterionSkill] — one per active criterion
    """

    def __init__(self, criterion: ReviewCriterion) -> None:
        self.criterion = criterion

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.criterion.name

    @property
    def material(self) -> str:
        return self.criterion.material

    # ── Scoring ───────────────────────────────────────────────────────────────

    def score(
        self,
        story: "Story",
        client: "OllamaClient",
        settings: "RuntimeSettings",
    ) -> CriterionScore:
        """Score this criterion for the given story via a dedicated Ollama call.

        Returns a heuristic score if the model call fails or returns unparseable
        output, so this method never raises.
        """
        body = str(story.get("body") or "")[:600]
        title = str(story.get("title") or "")
        prompt = self._build_prompt(title, body)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a media analyst. Score the article on one specific criterion only. "
                    "Return exactly two lines: score=<integer 1-10> and reason=<max 12 words>. "
                    "No other output."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            raw = client.chat(messages, num_predict=40, json_format=False)
            return self._parse_response(raw, story)
        except Exception as exc:
            log.warning(
                "CriterionSkill %s failed for story %s: %s — using heuristic.",
                self.name,
                story.get("id"),
                exc,
            )
            return self.heuristic(story)

    def heuristic(self, story: "Story") -> CriterionScore:
        """Return a keyword-based score without calling the model."""
        text = f"{story.get('title', '')} {story.get('body', '')}".lower()
        return {
            "criterion": self.criterion.name,
            "material": self.criterion.material,
            "score": _heuristic_score(text, self.criterion.name, self.criterion.material),
            "reason": f"Heuristic fallback for {self.criterion.name}.",
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_prompt(self, title: str, body: str) -> str:
        return (
            f"Criterion: {self.criterion.name}\n"
            f"What to detect: {self.criterion.material}\n"
            f"Scoring scale: {self.criterion.scale_description}\n\n"
            f"Return exactly:\n"
            f"score=<integer 1-10>\n"
            f"reason=<why, max 12 words>\n\n"
            f"Article title: {title}\n"
            f"Article text: {body}"
        )

    def _parse_response(self, raw: str, story: "Story") -> CriterionScore:
        score = None
        reason = "Model score."
        for line in raw.strip().splitlines():
            line = clean_whitespace(line)
            if line.startswith("score="):
                try:
                    score = max(1, min(10, int(line.split("=", 1)[1].strip())))
                except ValueError:
                    pass
            elif line.startswith("reason="):
                reason = shorten_for_display(
                    line.split("=", 1)[1].strip(), max_length=120
                )
        if score is None:
            log.debug(
                "CriterionSkill %s: could not parse score from model output; using heuristic.",
                self.name,
            )
            return self.heuristic(story)
        return {
            "criterion": self.criterion.name,
            "material": self.criterion.material,
            "score": score,
            "reason": reason,
        }

    def __repr__(self) -> str:
        return f"CriterionSkill(name={self.name!r})"


def load_skills(criteria: list[ReviewCriterion]) -> list[CriterionSkill]:
    """Create one CriterionSkill per criterion — the standard skill set."""
    return [CriterionSkill(criterion) for criterion in criteria]
