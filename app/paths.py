"""Locations of settings, logs, history and models."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "AppLiveCaption"


def _env_dir(var: str, fallback: str) -> Path:
    base = os.environ.get(var) or str(Path.home() / fallback)
    return Path(base) / APP_NAME


def roaming_dir() -> Path:
    """%APPDATA%\\AppLiveCaption: settings, history and logs."""
    d = _env_dir("APPDATA", "AppData/Roaming")
    d.mkdir(parents=True, exist_ok=True)
    return d


def local_dir() -> Path:
    """%LOCALAPPDATA%\\AppLiveCaption: large machine-local data."""
    d = _env_dir("LOCALAPPDATA", "AppData/Local")
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_models_dir() -> Path:
    return local_dir() / "models"


def config_file() -> Path:
    return roaming_dir() / "config.json"


def history_dir() -> Path:
    d = roaming_dir() / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = roaming_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_dir() -> Path:
    """Folder holding bundled resources (the ``app`` package, or the PyInstaller bundle)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
    return Path(__file__).resolve().parent
