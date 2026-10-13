#!/usr/bin/env python3
"""Build the MkDocs manual and include local source files linked as evidence."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SITE = ROOT / "site"
LINK_RE = re.compile(r"!?\[[^\]]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)")


def linked_local_files() -> set[Path]:
    linked: set[Path] = set()
    for markdown in DOCS.rglob("*.md"):
        for match in LINK_RE.finditer(markdown.read_text(encoding="utf-8")):
            destination = match.group(1).strip("<>")
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (markdown.parent / unquote(parsed.path)).resolve()
            if not target.is_file():
                raise SystemExit(f"Referencia local inexistente: {markdown.relative_to(ROOT)} → {destination}")
            try:
                target.relative_to(DOCS.resolve())
                continue
            except ValueError:
                pass
            try:
                target.relative_to(ROOT)
            except ValueError as exc:
                raise SystemExit(f"La referencia sale del repositorio: {markdown} → {destination}") from exc
            linked.add(target)
    return linked


def main() -> int:
    references = linked_local_files()
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict"], cwd=ROOT, check=False
    )
    if result.returncode:
        return result.returncode
    for source in references:
        destination = SITE / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    print(f"Incluidas {len(references)} referencias locales de código y ejemplos en {SITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
