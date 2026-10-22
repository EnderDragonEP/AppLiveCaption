"""Local model storage: where models live and whether they are complete."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .catalog import ModelInfo

MARKER = ".alc_complete.json"
PARTIAL_SUFFIX = ".part"
# A CTranslate2 (faster-whisper) model. Without tokenizer.json faster-whisper
# would fetch a tokenizer from the internet, which offline use can't allow.
ESSENTIAL_FILES = ("config.json", "model.bin", "tokenizer.json")


def models_dir() -> Path:
    """The configured model folder (falls back to the default location)."""
    from ..paths import default_models_dir, from_setting

    try:
        from ..config import cfg

        value = cfg.get(cfg.modelFolder)
        path = from_setting(value) if value else default_models_dir()
    except Exception:  # noqa: BLE001 - config unavailable (e.g. very early start-up)
        path = default_models_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_path(info: ModelInfo, base: Path | None = None) -> Path:
    return (base or models_dir()) / info.folder_name


def _essential_files(info: ModelInfo) -> list[str]:
    return list(ESSENTIAL_FILES)


def is_downloaded(info: ModelInfo, base: Path | None = None) -> bool:
    d = model_path(info, base)
    if not d.is_dir():
        return False
    if (d / MARKER).is_file():
        return True
    # Accept a model folder that was filled by other tools, as long as no
    # download is half-finished.
    if any(d.rglob(f"*{PARTIAL_SUFFIX}")):
        return False
    return all((d / f).is_file() for f in _essential_files(info))


def has_partial_download(info: ModelInfo, base: Path | None = None) -> bool:
    d = model_path(info, base)
    return d.is_dir() and not is_downloaded(info, base) and any(d.iterdir())


def write_marker(info: ModelInfo, files: dict[str, int], base: Path | None = None) -> None:
    d = model_path(info, base)
    (d / MARKER).write_text(json.dumps({"repo": info.repo, "files": files}, indent=1), encoding="utf-8")


def verify(info: ModelInfo, base: Path | None = None) -> str | None:
    """Return a description of the problem if files are missing or truncated, else None."""
    d = model_path(info, base)
    if not d.is_dir():
        return "missing"
    marker = d / MARKER
    if marker.is_file():
        try:
            files = json.loads(marker.read_text(encoding="utf-8")).get("files", {})
        except (OSError, ValueError):
            files = {}
        for name, size in files.items():
            p = d / name
            if not p.is_file():
                return f"missing file {name}"
            if size and p.stat().st_size != size:
                return f"incomplete file {name}"
    for f in _essential_files(info):
        if not (d / f).is_file():
            return f"missing file {f}"
    return None


def disk_usage(info: ModelInfo, base: Path | None = None) -> int:
    d = model_path(info, base)
    if not d.is_dir():
        return 0
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())


def delete_model(info: ModelInfo, base: Path | None = None) -> None:
    d = model_path(info, base)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
