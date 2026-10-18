"""The model catalog, loaded from catalog.json."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..asr.languages import WHISPER_LANGUAGES

BACKEND_FASTER_WHISPER = "faster-whisper"
BACKEND_TRANSFORMERS = "transformers"

# Rough relative compute cost, used for the "too slow for real time" warning.
TIER_COST = {"tiny": 1, "base": 2, "small": 5, "medium": 12, "turbo": 10, "large": 25}

_DEFAULT_IGNORE = ["*.md", ".gitattributes", "*.png", "*.jpg", "LICENSE*"]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    repo: str
    backend: str
    languages: tuple[str, ...]
    size_mb: int
    license: str
    tier: str = "large"
    description: str = ""
    language_notes: dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    initial_prompt: str = ""
    files: tuple[str, ...] = ()  # allow patterns; empty means "everything except docs"

    @property
    def folder_name(self) -> str:
        return self.repo.replace("/", "--")

    @property
    def size_gb(self) -> float:
        return self.size_mb / 1024

    @property
    def cost(self) -> int:
        return TIER_COST.get(self.tier, 25)

    @property
    def ignore_patterns(self) -> list[str]:
        return [] if self.files else list(_DEFAULT_IGNORE)

    def language_label(self, code: str) -> str:
        from ..asr.languages import language_name

        note = self.language_notes.get(code)
        return note or language_name(code)


def _catalog_path() -> Path:
    return Path(__file__).with_name("catalog.json")


@lru_cache(maxsize=1)
def load_catalog() -> tuple[ModelInfo, ...]:
    data = json.loads(_catalog_path().read_text(encoding="utf-8"))
    out = []
    for m in data["models"]:
        langs = m.get("languages", "whisper")
        languages = tuple(WHISPER_LANGUAGES) if langs == "whisper" else tuple(langs)
        out.append(ModelInfo(
            id=m["id"],
            name=m["name"],
            repo=m["repo"],
            backend=m["backend"],
            languages=languages,
            size_mb=int(m.get("size_mb", 0)),
            license=m.get("license", ""),
            tier=m.get("tier", "large"),
            description=m.get("description", ""),
            language_notes=dict(m.get("language_notes", {})),
            initial_prompt=m.get("initial_prompt", ""),
            files=tuple(m.get("files", ())),
        ))
    return tuple(out)


def get_model(model_id: str) -> ModelInfo | None:
    for m in load_catalog():
        if m.id == model_id:
            return m
    return None
