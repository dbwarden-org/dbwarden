"""Pre-build script: concatenates all markdown docs into llms-full.txt.

Run before `zensical build` to produce a single plain-text file containing
the full documentation, suitable for LLM context ingestion.

Output is placed in docs/llms-full.txt and included in the rendered site.
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"
OUTPUT = DOCS_DIR / "llms-full.txt"
SITE_URL = "https://dbwarden.org"

SKIP_FILES = {
    "llms.txt",
    "llms-full.txt",
}
SKIP_DIRS = {"overrides", "stylesheets"}


def route_path_from_file(filepath: Path) -> str:
    rel = filepath.relative_to(DOCS_DIR)
    parts = list(rel.parts)
    if parts[-1] == "index.md":
        parts.pop()
        if not parts:
            return "/"
        return "/" + "/".join(parts) + "/"
    stem = Path(*parts).with_suffix("")
    return "/" + str(stem) + "/"


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def main() -> int:
    md_files = sorted(DOCS_DIR.rglob("*.md"))
    md_files = [
        f for f in md_files
        if f.name not in SKIP_FILES
        and not any(d in f.parts for d in SKIP_DIRS)
    ]

    sections = [
        "# DBWarden Documentation",
        "> Full documentation for DBWarden - a SQL-first database migration system",
        f"> Source: {SITE_URL}",
        f"> Pages: {len(md_files)}",
        "",
    ]

    for md_file in md_files:
        route = route_path_from_file(md_file)
        url = SITE_URL.rstrip("/") + route
        raw = md_file.read_text(encoding="utf-8")
        body = strip_frontmatter(raw).strip()

        sections.append("=" * 72)
        sections.append(f"PAGE: {url}")
        sections.append("=" * 72)
        sections.append("")
        sections.append(body)
        sections.append("")

    OUTPUT.write_text("\n".join(sections), encoding="utf-8")
    size_kb = OUTPUT.stat().st_size / 1024
    print(f"Written {OUTPUT} ({len(md_files)} pages, {size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
