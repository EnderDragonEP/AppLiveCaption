"""faster-whisper (CTranslate2) backend."""

from __future__ import annotations

import gc
import logging

import numpy as np

from .backend import ASRBackend, GpuFailure, ModelLoadError, OutOfMemory, Transcript, UnsupportedLanguage, is_oom_error
from .cuda_setup import setup_cuda_dlls
from .hardware import cuda_compute_type, cuda_device_index

log = logging.getLogger(__name__)


class FasterWhisperBackend(ASRBackend):
    def __init__(self):
        self.model = None
        self._languages: list[str] | None = None

    def load(self, model_dir: str, device: str) -> None:
        setup_cuda_dlls()
        from faster_whisper import WhisperModel

        self.device = device
        self.compute_type = cuda_compute_type() if device == "cuda" else "int8"
        try:
            self.model = WhisperModel(model_dir, device=device, compute_type=self.compute_type,
                                      device_index=cuda_device_index() if device == "cuda" else 0,
                                      cpu_threads=0, num_workers=1, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            self.model = None
            if is_oom_error(exc):
                raise OutOfMemory(str(exc)) from exc
            raise ModelLoadError(str(exc)) from exc
        try:
            self._languages = list(self.model.supported_languages)
        except Exception:  # noqa: BLE001
            self._languages = None
        log.info("faster-whisper model loaded from %s on %s/%s", model_dir, device, self.compute_type)

    def unload(self) -> None:
        self.model = None
        gc.collect()

    def supported_languages(self) -> list[str] | None:
        return self._languages

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None, final: bool,
                   translate: bool = False) -> Transcript:
        if self.model is None:
            raise RuntimeError("model not loaded")
        if language and self._languages is not None and language not in self._languages:
            raise UnsupportedLanguage(language)
        opts = dict(
            language=language or None,
            task="translate" if translate else "transcribe",  # (English-only models always transcribe)
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            word_timestamps=False,
            suppress_blank=True,
            log_progress=False,
        )
        if final:
            opts.update(beam_size=5, best_of=5, temperature=[0.0, 0.2, 0.4, 0.6],
                        compression_ratio_threshold=2.4, log_prob_threshold=-1.0, no_speech_threshold=0.6)
        else:
            # Greedy and no temperature fallback: partials must be quick.
            opts.update(beam_size=1, best_of=1, temperature=0.0)
        try:
            segments, info = self.model.transcribe(audio, **opts)
            segments = list(segments)
        except Exception as exc:  # noqa: BLE001
            if is_oom_error(exc):
                raise OutOfMemory(str(exc)) from exc
            if self.device == "cuda" and "CUDA failed" in str(exc):
                raise GpuFailure(str(exc)) from exc
            raise
        text = "".join(s.text for s in segments).strip()
        if segments:
            no_speech = max(s.no_speech_prob for s in segments)
            avg_lp = float(np.mean([s.avg_logprob for s in segments]))
            comp = max(s.compression_ratio for s in segments)
        else:
            no_speech, avg_lp, comp = 1.0, -10.0, 1.0
        return Transcript(
            text=text,
            language=info.language,
            language_probability=info.language_probability,
            no_speech_prob=no_speech,
            avg_logprob=avg_lp,
            compression_ratio=comp,
            segments=[(s.start, s.end, s.text) for s in segments],
        )
