from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from utils import clean_whitespace, project_root, safe_filename


@dataclass(frozen=True)
class ReviewCriterion:
    name: str
    material: str
    anchor_low: str = ""
    anchor_mid: str = ""
    anchor_high: str = ""

    @property
    def question(self) -> str:
        return f"Does this article contain {self.material} material?"

    @property
    def scale_description(self) -> str:
        if self.anchor_low and self.anchor_mid and self.anchor_high:
            return f"1-3: {self.anchor_low} | 4-6: {self.anchor_mid} | 7-10: {self.anchor_high}"
        return "1-2 = not present, 3-4 = minor/tangential, 5-6 = moderate, 7-8 = strong/clear, 9-10 = dominant/extreme"


def load_review_criteria(path: str | Path) -> list[ReviewCriterion]:
    criteria_path = Path(path)
    if not criteria_path.is_absolute():
        criteria_path = project_root() / criteria_path
    if not criteria_path.exists():
        raise FileNotFoundError(f"Review criteria file not found: {criteria_path}")

    criteria: list[ReviewCriterion] = []
    for raw_line in criteria_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue

        parts = [clean_whitespace(part) for part in line.split("|")]
        parts = [part for part in parts if part]
        if not parts:
            continue

        if len(parts) == 1:
            material = parts[0]
            name = safe_filename(material, max_length=48).replace("-", "_")
        else:
            name = safe_filename(parts[0], max_length=48).replace("-", "_")
            material = parts[1]

        anchor_low = parts[2] if len(parts) > 2 else ""
        anchor_mid = parts[3] if len(parts) > 3 else ""
        anchor_high = parts[4] if len(parts) > 4 else ""

        criteria.append(ReviewCriterion(
            name=name or "criterion",
            material=material,
            anchor_low=anchor_low,
            anchor_mid=anchor_mid,
            anchor_high=anchor_high,
        ))

    if not criteria:
        raise RuntimeError(f"No review criteria were found in: {criteria_path}")
    return criteria
