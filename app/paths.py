"""Locations of settings, history, logs, models and GPU support.

The app is portable: it keeps everything it writes next to AppLiveCaption.exe
(the project folder when running from source), so moving or deleting that
folder moves or removes all of it:

    config.json   settings
    history\\      captioning sessions
    logs\\         app.log
    models\\       downloaded models (Settings can point elsewhere)
    cuda\\         GPU support (app/asr/cuda_libs.py)

If that folder can't be written (e.g. under C:\\Program Files), the app uses
%LOCALAPPDATA%\\AppLiveCaption instead. ALC_DATA_DIR overrides the location
(the tests use it).
"""

from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

APP_NAME = "AppLiveCaption"


def app_dir() -> Path:
    """The folder with AppLiveCaption.exe, or the project folder when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=None)
def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return False
    return True


def data_dir() -> Path:
    """The folder that holds everything the app writes (see the module docstring)."""
    override = os.environ.get("ALC_DATA_DIR")
    if override:
        d = Path(override)
    elif _writable(app_dir()):
        return app_dir()
    else:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _subdir(name: str) -> Path:
    d = data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return data_dir() / "config.json"


def history_dir() -> Path:
    return _subdir("history")


def log_dir() -> Path:
    return _subdir("logs")


def default_models_dir() -> Path:
    return data_dir() / "models"


def to_setting(path: str | Path) -> str:
    """A folder as saved in the settings: relative to the data folder when it's
    inside it, so the setting still works after the app folder is moved."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(data_dir().resolve()))
    except ValueError:
        return str(p)


def from_setting(value: str) -> Path:
    """The folder a saved setting refers to (see ``to_setting``)."""
    p = Path(value)
    return p if p.is_absolute() else data_dir() / p


def resource_dir() -> Path:
    """Folder holding bundled resources (the ``app`` package, or the PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
    return Path(__file__).resolve().parent
