"""Creates the right backend for a catalog entry."""

from __future__ import annotations

from ..models.catalog import BACKEND_FASTER_WHISPER, BACKEND_TRANSFORMERS, ModelInfo
from .backend import ASRBackend


class BackendUnavailable(RuntimeError):
    """The backend's Python packages are not installed."""


def create_backend(info: ModelInfo) -> ASRBackend:
    if info.backend == BACKEND_FASTER_WHISPER:
        from .faster_whisper_backend import FasterWhisperBackend

        return FasterWhisperBackend()
    if info.backend == BACKEND_TRANSFORMERS:
        try:
            from .transformers_backend import TransformersBackend
        except ImportError as exc:
            raise BackendUnavailable(str(exc)) from exc
        return TransformersBackend()
    raise BackendUnavailable(f"unknown backend {info.backend}")
