from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from utils import clean_whitespace, project_root, safe_filename


@dataclass(frozen=True)
class ReviewCriterion:
    name: str
    material: str

    @property
    def question(self) -> str:
        return f"Does this article contain {self.material} material?"


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

        criteria.append(ReviewCriterion(name=name or "criterion", material=material))

    if not criteria:
        raise RuntimeError(f"No review criteria were found in: {criteria_path}")
    return criteria
