"""Rotating file log in the logs folder next to the exe (app/paths.py)."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import log_dir


def setup_logging(console: bool = False, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_alc_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(log_dir() / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console and sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)
    # comtypes logs every AddRef/Release at DEBUG; keep it quiet.
    # faster_whisper logs every call ("Processing audio with duration ...").
    for noisy in ("comtypes", "httpx", "httpcore", "urllib3", "huggingface_hub", "filelock", "PIL", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._alc_configured = True  # type: ignore[attr-defined]
