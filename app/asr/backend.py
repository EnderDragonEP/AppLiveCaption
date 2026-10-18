"""Speech-to-text backend interface."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Transcript:
    text: str
    language: str | None = None
    language_probability: float | None = None
    # Whisper's own confidence signals, used by the hallucination filter.
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    compression_ratio: float = 1.0
    segments: list[tuple[float, float, str]] = field(default_factory=list)


class OutOfMemory(RuntimeError):
    """The model does not fit in GPU memory."""


class GpuFailure(RuntimeError):
    """The GPU failed in a way that can't be recovered on the GPU (e.g. a CUDA
    device-side assert); the model should be reloaded on the CPU."""


class ModelLoadError(RuntimeError):
    """Model files are corrupt, incomplete or incompatible."""


class UnsupportedLanguage(ValueError):
    pass


class ASRBackend(abc.ABC):
    """A loaded model that can transcribe 16 kHz mono float32 audio."""

    device: str = "cpu"
    compute_type: str = ""

    @abc.abstractmethod
    def load(self, model_dir: str, device: str) -> None:
        """Load from a local folder. ``device`` is "cuda" or "cpu".

        Raises OutOfMemory or ModelLoadError.
        """

    @abc.abstractmethod
    def unload(self) -> None:
        """Release the model and free GPU memory."""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None, final: bool) -> Transcript:
        """Transcribe one utterance.

        ``final`` asks for the more accurate (slower) settings; partial
        updates use greedy decoding. Raises OutOfMemory.
        """

    def supported_languages(self) -> list[str] | None:
        """Language codes the loaded model's tokenizer accepts (None = unknown)."""
        return None


def is_oom_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda_error_out_of_memory" in msg or "cublas_status_alloc_failed" in msg


def zlib_compression_ratio(text: str) -> float:
    import zlib

    data = text.encode("utf-8")
    return len(data) / max(1, len(zlib.compress(data)))
