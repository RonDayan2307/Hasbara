from datetime import datetime
from pathlib import Path


def write_digest(summary: str, stories: list[dict]) -> Path:
    desktop = Path.home() / "Desktop"
    filename = f"news_digest_{datetime.now().strftime('%Y-%m-%d')}.txt"
    outpath = desktop / filename

    lines = [
        "Daily News Digest",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "=" * 80,
        "",
        summary,
        "",
        "=" * 80,
        "",
        "Sources Used",
        ""
    ]

    for story in stories:
        lines.append(f"- {story['source']}: {story['title']}")
        lines.append(f"  {story['url']}")
        lines.append("")

    outpath.write_text("\n".join(lines), encoding="utf-8")
    return outpath