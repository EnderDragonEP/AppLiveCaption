"""Creates the right backend for a catalog entry."""

from __future__ import annotations

from ..models.catalog import BACKEND_FASTER_WHISPER, ModelInfo
from .backend import ASRBackend


class BackendUnavailable(RuntimeError):
    """The backend's Python packages are not installed."""


def create_backend(info: ModelInfo) -> ASRBackend:
    if info.backend == BACKEND_FASTER_WHISPER:
        from .faster_whisper_backend import FasterWhisperBackend

        return FasterWhisperBackend()
    raise BackendUnavailable(f"unknown backend {info.backend}")
