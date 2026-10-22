"""Locations of settings, history, logs, models and GPU support.

The portable version keeps everything it writes next to AppLiveCaption.exe
(the project folder when running from source), so moving or deleting that
folder moves or removes all of it:

    config.json   settings
    models.json   the user's changes to the model list
    history\\      captioning sessions
    logs\\         app.log
    models\\       downloaded models (Settings can point elsewhere)
    cuda\\         GPU support (app/asr/cuda_libs.py)

The installed version (Setup puts INSTALLED_MARKER next to the exe, see
installer/AppLiveCaption.iss) splits them the Windows way: config.json,
models.json and history\\ in %APPDATA%\\AppLiveCaption (data_dir; it roams with
the user's profile), and the big or PC-specific logs\\, models\\ and cuda\\ in
%LOCALAPPDATA%\\AppLiveCaption (local_data_dir; a roaming profile would copy
gigabytes of models at every sign-in).

If a portable copy's folder can't be written (e.g. under C:\\Program Files),
the app uses %LOCALAPPDATA%\\AppLiveCaption for all of it. ALC_DATA_DIR
overrides the location of all of it (the tests use it).
"""

from __future__ import annotations

import os
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

APP_NAME = "AppLiveCaption"
INSTALLED_MARKER = "AppLiveCaption.installed"  # next to the exe of an installed copy


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


@lru_cache(maxsize=None)
def is_installed() -> bool:
    """True for a copy installed with Setup (never when running from source)."""
    return bool(getattr(sys, "frozen", False)) and (app_dir() / INSTALLED_MARKER).is_file()


def _roaming_app_data() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME


def data_dir() -> Path:
    """The folder for settings, history and the model list (see the module docstring)."""
    override = os.environ.get("ALC_DATA_DIR")
    if override:
        d = Path(override)
    elif is_installed():
        d = _roaming_app_data()
    elif _writable(app_dir()):
        return app_dir()
    else:
        d = _local_app_data()
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_data_dir() -> Path:
    """The folder for logs, models and GPU support: %LOCALAPPDATA% when installed, else ``data_dir()``."""
    if os.environ.get("ALC_DATA_DIR") or not is_installed():
        return data_dir()
    d = _local_app_data()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _subdir(base: Path, name: str) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_file() -> Path:
    return data_dir() / "config.json"


def history_dir() -> Path:
    return _subdir(data_dir(), "history")


def log_dir() -> Path:
    return _subdir(local_data_dir(), "logs")


def default_models_dir() -> Path:
    return local_data_dir() / "models"


def to_setting(path: str | Path) -> str:
    """The model folder as saved in the settings: relative to the folder models
    live in by default when it's inside it, so the setting still works after a
    portable copy's folder is moved."""
    p = Path(path).resolve()
    try:
        return str(p.relative_to(local_data_dir().resolve()))
    except ValueError:
        return str(p)


def from_setting(value: str) -> Path:
    """The folder a saved setting refers to (see ``to_setting``)."""
    p = Path(value)
    return p if p.is_absolute() else local_data_dir() / p


def resource_dir() -> Path:
    """Folder holding bundled resources (the ``app`` package, or the PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
    return Path(__file__).resolve().parent
