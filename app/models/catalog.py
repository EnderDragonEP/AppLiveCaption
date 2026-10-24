"""The model catalog: the presets in catalog.json, changed by the user's models.json.

models.json (in the data folder, see app/paths.py) holds what the user did to the list:
the presets they removed and the models they added. catalog.json is never written.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..asr.languages import WHISPER_LANGUAGES
from ..paths import data_dir

log = logging.getLogger(__name__)

BACKEND_FASTER_WHISPER = "faster-whisper"

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
    # Files fetched from another repo, {file name: repo}, for a repo that lacks one.
    extra_files: dict[str, str] = field(default_factory=dict, hash=False, compare=False)
    # False when Whisper's language detection doesn't work for the model's
    # language (e.g. Taigi): the model then always uses its first language.
    auto_detect: bool = True
    # False when the model wasn't trained to translate into English (large-v3-turbo, the fine-tunes): it's
    # still allowed to try, with a warning, but it mostly writes the spoken language.
    translate: bool = True
    # Added by the user (not a preset).
    custom: bool = False
    # The folder on this PC a model was added from. It's copied into the models
    # folder instead of downloaded (see downloader.download_model), again after
    # its files were deleted, so it's kept.
    source_folder: str = ""

    @property
    def default_language(self) -> str | None:
        """The language to decode with when the user picked "auto" (None = detect)."""
        return None if self.auto_detect or not self.languages else self.languages[0]

    @property
    def folder_name(self) -> str:
        # A model imported from a local folder has no repository.
        return self.repo.replace("/", "--") if self.repo else _local_folder_name(self.id)

    @property
    def size_gb(self) -> float:
        return self.size_mb / 1024

    @property
    def cost(self) -> int:
        return TIER_COST.get(self.tier, 25)

    def language_label(self, code: str) -> str:
        from ..asr.languages import language_name

        note = self.language_notes.get(code)
        return note or language_name(code)


def _local_folder_name(model_id: str) -> str:
    return f"local--{model_id}"


def _catalog_path() -> Path:
    return Path(__file__).with_name("catalog.json")


def _user_path() -> Path:
    return data_dir() / "models.json"


def _parse(m: dict, custom: bool = False) -> ModelInfo:
    langs = m.get("languages", "whisper")
    languages = tuple(WHISPER_LANGUAGES) if langs == "whisper" else tuple(langs)
    return ModelInfo(
        id=m["id"],
        name=m["name"],
        repo=m.get("repo", ""),
        backend=m.get("backend", BACKEND_FASTER_WHISPER),
        languages=languages,
        size_mb=int(m.get("size_mb", 0)),
        license=m.get("license", ""),
        tier=m.get("tier", "large"),
        description=m.get("description", ""),
        language_notes=dict(m.get("language_notes", {})),
        initial_prompt=m.get("initial_prompt", ""),
        files=tuple(m.get("files", ())),
        extra_files=dict(m.get("extra_files", {})),
        auto_detect=bool(m.get("auto_detect", True)),
        translate=bool(m.get("translate", True)),
        custom=custom,
        source_folder=m.get("source_folder", ""),
    )


def _presets() -> list[dict]:
    return json.loads(_catalog_path().read_text(encoding="utf-8"))["models"]


def _read_user() -> dict:
    """The user's changes: {"removed": [preset ids], "custom": [catalog entries]}."""
    empty = {"removed": [], "custom": []}
    try:
        data = json.loads(_user_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except (OSError, ValueError):
        log.warning("Ignoring %s: it isn't valid", _user_path(), exc_info=True)
        return empty
    if not isinstance(data, dict):
        log.warning("Ignoring %s: it isn't a JSON object", _user_path())
        return empty

    def items(key: str) -> list:
        value = data.get(key, [])
        return value if isinstance(value, list) else []

    return {
        "removed": [i for i in items("removed") if isinstance(i, str)],
        "custom": [m for m in items("custom") if isinstance(m, dict) and m.get("id") and m.get("name")],
    }


def _write_user(data: dict) -> None:
    path = _user_path()
    if not data["removed"] and not data["custom"]:
        path.unlink(missing_ok=True)  # back to the default list: leave no file behind
    else:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    load_catalog.cache_clear()


@lru_cache(maxsize=1)
def load_catalog() -> tuple[ModelInfo, ...]:
    user = _read_user()
    presets = _presets()
    out = [_parse(m) for m in presets if m["id"] not in user["removed"]]
    # Ids and repositories of every preset, listed or removed. An added model with
    # one of them (a later catalog.json can add it as a preset) would be listed
    # twice, or share the preset's folder, so the preset wins.
    ids = {m["id"] for m in presets}
    repos = {m["repo"].lower() for m in presets if m.get("repo")}
    for m in user["custom"]:
        try:
            info = _parse(m, custom=True)
            repo = info.repo.lower()
        except (AttributeError, KeyError, TypeError, ValueError):
            log.warning("Ignoring a malformed model in models.json: %r", m)
            continue
        if info.id in ids or repo in repos:
            log.warning("Ignoring %s in models.json: a preset or another added model has its id or repository",
                        info.id)
            continue
        ids.add(info.id)
        if repo:
            repos.add(repo)
        out.append(info)
    return tuple(out)


def get_model(model_id: str) -> ModelInfo | None:
    for m in load_catalog():
        if m.id == model_id:
            return m
    return None


def is_default_catalog() -> bool:
    """True when the list is exactly the presets (nothing removed, nothing added)."""
    user = _read_user()
    return not user["removed"] and not user["custom"]


def unique_id(base: str, local: bool = False) -> str:
    """``base`` made safe for a folder name and unused by any model in the list or the presets.

    For a model from a folder on this PC (``local``), whose files go in a folder
    named after its id, the id also isn't one whose folder is still on disk: those
    files were left by a model that was removed, and would be taken for its own.
    """
    from .storage import models_dir  # (storage imports this module)

    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "model"
    taken = {m["id"] for m in _presets()} | {m["id"] for m in _read_user()["custom"]}
    base_dir = models_dir() if local else None
    candidate, n = slug, 2
    while candidate in taken or (base_dir is not None and (base_dir / _local_folder_name(candidate)).exists()):
        candidate, n = f"{slug}-{n}", n + 1
    return candidate


def preset_for_repo(repo: str) -> dict | None:
    """The preset (listed or removed) that downloads from ``repo``, if there is one."""
    return next((p for p in _presets() if repo and p.get("repo", "").lower() == repo.lower()), None)


def add_custom_model(entry: dict) -> ModelInfo:
    """Add a model to the list. If ``entry`` is a removed preset's repository, that preset comes back
    instead; if it's the repository of a model in the list, that model is returned and nothing changes."""
    user = _read_user()
    repo = entry.get("repo", "")
    preset = preset_for_repo(repo)
    if preset is not None and preset["id"] in user["removed"]:
        user["removed"].remove(preset["id"])
        _write_user(user)
        return get_model(preset["id"])
    listed = next((m for m in load_catalog() if repo and m.repo.lower() == repo.lower()), None)
    if listed is not None:
        return listed
    if not str(entry.get("name", "")).strip():
        raise ValueError("a model needs a name")  # models.json would drop it
    entry = dict(entry, id=unique_id(entry.get("id") or entry["name"], local=not repo))
    info = _parse(entry, custom=True)  # raises if it's malformed, before it's saved
    user["custom"].append(entry)
    _write_user(user)
    return info


def remove_model(model_id: str) -> None:
    """Take a model off the list: a preset is remembered as removed, an added one is forgotten."""
    info = get_model(model_id)
    if info is None:
        return
    user = _read_user()
    if info.custom:
        user["custom"] = [m for m in user["custom"] if m["id"] != model_id]
    elif model_id not in user["removed"]:
        user["removed"].append(model_id)
    _write_user(user)


def reset_catalog() -> list[ModelInfo]:
    """Back to the presets. Returns the added models that were dropped (their files may remain on disk)."""
    dropped = [m for m in load_catalog() if m.custom]
    _write_user({"removed": [], "custom": []})
    return dropped
