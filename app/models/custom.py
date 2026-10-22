"""Checking a model the user wants to add: a Hugging Face repository or a local folder.

Both produce a catalog entry (the format of catalog.json) for ``catalog.add_custom_model``.
Only CTranslate2 (faster-whisper) models can be added: model.bin, config.json and
tokenizer.json must be there. Without tokenizer.json, faster-whisper would fetch a
tokenizer from the internet, which offline use can't allow.
"""

from __future__ import annotations

import re
from pathlib import Path

from .catalog import BACKEND_FASTER_WHISPER, preset_for_repo
from .downloader import allow_network, local_files, select_files
from .storage import ESSENTIAL_FILES


class CustomModelError(Exception):
    """The model can't be added; the message says why, in words for the user."""


def check_files(names: list[str], local: bool = False) -> str | None:
    """A description of what's wrong with a model's file list, or None when it can run on faster-whisper."""
    have = {Path(n).name for n in names if "/" not in n.replace("\\", "/")}
    missing = [f for f in ESSENTIAL_FILES if f not in have]
    if not missing:
        return None
    if "model.bin" in missing:
        hint = ("Pick the folder that holds the converted model, or convert it with ct2-transformers-converter."
                if local else "Look for a \"faster-whisper\" conversion of it on Hugging Face, or convert it with "
                "ct2-transformers-converter.")
        return f"This isn't a faster-whisper (CTranslate2) model: model.bin is missing. {hint}"
    return f"The model is incomplete: {', '.join(missing)} is missing."


def estimate_tier(model_bin_bytes: int) -> str:
    """Which "may be too slow" tier fits a model of this size (a rough guess from the weights' size)."""
    mb = model_bin_bytes / 2**20
    for limit, tier in ((120, "tiny"), (300, "base"), (1024, "small"), (2048, "medium")):
        if mb <= limit:
            return tier
    return "large"


def parse_repo_id(text: str) -> str:
    """"owner/name" from what the user typed: the ID itself or a huggingface.co link."""
    text = text.strip().rstrip("/")
    text = re.sub(r"^https?://(www\.)?huggingface\.co/", "", text)
    text = re.sub(r"/(tree|blob|resolve)/.*$", "", text)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        raise CustomModelError("Enter a Hugging Face repository like Systran/faster-whisper-large-v3.")
    return text


def _entry(name: str, repo: str, size_bytes: int, model_bin: int, license: str, description: str, **extra) -> dict:
    return {
        "name": name,
        "repo": repo,
        "backend": BACKEND_FASTER_WHISPER,
        "languages": "whisper",  # narrowed to what the model reports when it's loaded
        "size_mb": max(1, round(size_bytes / 2**20)),
        "license": license or "See the model page",
        "tier": estimate_tier(model_bin),
        "description": description,
        **extra,
    }


def inspect_repo(text: str, name: str = "") -> dict:
    """A catalog entry for a Hugging Face repository (needs the network, unless it's a preset's).

    Raises CustomModelError.
    """
    from huggingface_hub import HfApi
    from huggingface_hub.errors import RepositoryNotFoundError

    repo = parse_repo_id(text)
    preset = preset_for_repo(repo)
    if preset is not None:
        # A preset's repository: add_custom_model brings the preset back if it was removed. (Its
        # files can't be checked here: some presets fetch a file from elsewhere, see extra_files.)
        return dict(preset)
    with allow_network():
        try:
            meta = HfApi().model_info(repo, files_metadata=True, timeout=30)
        except RepositoryNotFoundError as exc:
            raise CustomModelError(f"{repo} wasn't found on Hugging Face (or it's private).") from exc
        except Exception as exc:  # noqa: BLE001 - network errors of all kinds
            raise CustomModelError(f"Couldn't reach Hugging Face: {exc}") from exc
    siblings = [(s.rfilename, s.size or 0) for s in meta.siblings or []]
    problem = check_files([n for n, _ in siblings])
    if problem:
        raise CustomModelError(problem)
    model_bin = next(size for n, size in siblings if n == "model.bin")
    size = sum(s for _, s in select_files(siblings))
    return _entry(name.strip() or repo.split("/", 1)[1], repo, size, model_bin, _repo_license(meta),
                  f"Added by you from huggingface.co/{repo}.")


def _repo_license(meta) -> str:
    card = getattr(meta, "card_data", None)
    value = getattr(card, "license", None) if card is not None else None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if not value:
        value = next((t.split(":", 1)[1] for t in getattr(meta, "tags", None) or [] if t.startswith("license:")), "")
    return str(value or "")


def inspect_folder(path: str, name: str = "") -> dict:
    """A catalog entry for a model folder on this PC. Raises CustomModelError."""
    folder = Path(path)
    if not folder.is_dir():
        raise CustomModelError("That folder doesn't exist.")
    files = local_files(folder)
    problem = check_files([n for n, _ in files], local=True)
    if problem:
        raise CustomModelError(problem)
    label = folder.name or "Local model"  # (a drive's root folder has no name)
    size = sum(s for _, s in select_files(files))
    return _entry(name.strip() or label, "", size, dict(files)["model.bin"], "Unknown", f"Added by you from {folder}.",
                  id=f"local-{label}", source_folder=str(folder.resolve()))
