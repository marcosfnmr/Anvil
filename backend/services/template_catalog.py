"""Versioned, built-in templates packaged with the backend image."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models.scenario import ScenarioCreate


TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"


class TemplateNotFoundError(LookupError):
    pass


class TemplateCatalogError(RuntimeError):
    pass


def _manifest() -> list[dict[str, Any]]:
    try:
        raw = json.loads((TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8"))
        entries = raw["templates"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise TemplateCatalogError(f"Unable to read scenario template catalog: {error}") from error
    if not isinstance(entries, list):
        raise TemplateCatalogError("Scenario template catalog must contain a templates list")
    ids: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise TemplateCatalogError("Scenario template entries need an id")
        if item["id"] in ids:
            raise TemplateCatalogError(f"Duplicate scenario template id '{item['id']}'")
        ids.add(item["id"])
        filename = item.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
            raise TemplateCatalogError(f"Invalid file name for scenario template '{item['id']}'")
    return entries


def list_templates() -> list[dict[str, Any]]:
    fields = ("id", "title", "category", "summary", "complexity", "tags")
    return [{key: item[key] for key in fields} for item in _manifest()]


def load_template(template_id: str) -> ScenarioCreate:
    entry = next((item for item in _manifest() if item["id"] == template_id), None)
    if entry is None:
        raise TemplateNotFoundError(f"Scenario template '{template_id}' was not found")
    try:
        content = json.loads((TEMPLATE_DIR / entry["file"]).read_text(encoding="utf-8"))
        return ScenarioCreate.model_validate(content)
    except (OSError, ValueError) as error:
        raise TemplateCatalogError(f"Scenario template '{template_id}' is invalid: {error}") from error
