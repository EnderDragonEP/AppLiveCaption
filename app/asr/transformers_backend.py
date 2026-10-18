"""Hugging Face transformers + PyTorch backend (for models such as Breeze ASR 25
that only ship in transformers format)."""

from __future__ import annotations

import gc
import logging

import numpy as np

from .backend import (
    ASRBackend,
    GpuFailure,
    ModelLoadError,
    OutOfMemory,
    Transcript,
    UnsupportedLanguage,
    is_oom_error,
    zlib_compression_ratio,
)

log = logging.getLogger(__name__)

# Import here so a missing install raises ImportError when the backend is created.
import torch  # noqa: E402
import transformers  # noqa: E402

transformers.logging.set_verbosity_error()
transformers.logging.disable_progress_bar()


def is_fatal_cuda_error(exc: BaseException) -> bool:
    """A device-side assert leaves the CUDA context unusable for this process."""
    msg = str(exc)
    return "device-side assert" in msg or "CUDA error" in msg or type(exc).__name__ == "AcceleratorError"


class TransformersBackend(ASRBackend):
    # Beam width for final captions. Beam search in transformers is much slower
    # than in CTranslate2, so a narrower beam keeps finals near real time.
    final_beams = 3

    def __init__(self):
        self.model = None
        self.processor = None
        self.dtype = torch.float32
        self._lang_codes: list[str] = []
        self._lang_ids: list[int] = []
        self._sot = None

    @staticmethod
    def _prefetch(model_dir: str) -> None:
        """Read the weights once so they are in the OS file cache.

        transformers copies tensors out of the memory-mapped safetensors file
        while holding the GIL; from a cold disk that can freeze the UI thread
        for over a second. Plain file reads release the GIL, so warming the
        cache first keeps those copies short.
        """
        import glob
        import os

        buf = memoryview(bytearray(16 << 20))
        for path in glob.glob(os.path.join(model_dir, "*.safetensors")):
            try:
                with open(path, "rb", buffering=0) as fh:
                    while fh.readinto(buf):
                        pass
            except OSError:
                log.debug("prefetch failed for %s", path, exc_info=True)

    def load(self, model_dir: str, device: str) -> None:
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        self._prefetch(model_dir)
        self.device = device
        self.dtype = torch.float16 if device == "cuda" else torch.float32
        self.compute_type = "float16" if device == "cuda" else "float32"
        try:
            self.processor = WhisperProcessor.from_pretrained(model_dir, local_files_only=True)
            model = WhisperForConditionalGeneration.from_pretrained(
                model_dir, dtype=self.dtype, local_files_only=True, low_cpu_mem_usage=True, use_safetensors=True)
            self.model = model.to(device).eval()
        except Exception as exc:  # noqa: BLE001
            self.model = None
            if isinstance(exc, torch.cuda.OutOfMemoryError) or is_oom_error(exc):
                self.unload()
                raise OutOfMemory(str(exc)) from exc
            raise ModelLoadError(str(exc)) from exc
        gen = self.model.generation_config
        lang_to_id = getattr(gen, "lang_to_id", None) or {}
        self._lang_codes = [k.strip("<|>") for k in lang_to_id]
        self._lang_ids = list(lang_to_id.values())
        self._sot = gen.decoder_start_token_id
        log.info("transformers model loaded from %s on %s/%s", model_dir, device, self.compute_type)

    def unload(self) -> None:
        self.model = None
        self.processor = None
        gc.collect()
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - the CUDA context may be broken after a GPU failure
            log.debug("empty_cache failed", exc_info=True)

    def supported_languages(self) -> list[str] | None:
        return self._lang_codes or None

    def _features(self, audio: np.ndarray):
        feats = self.processor.feature_extractor(audio, sampling_rate=16000, return_tensors="pt").input_features
        return feats.to(self.device, dtype=self.dtype)

    def _detect_language(self, feats) -> tuple[str | None, float]:
        if not self._lang_ids:
            return None, 0.0
        enc = self.model.model.encoder(feats)
        dec = torch.tensor([[self._sot]], device=self.device)
        logits = self.model(encoder_outputs=enc, decoder_input_ids=dec).logits[0, -1]
        probs = logits[self._lang_ids].float().softmax(-1)
        i = int(probs.argmax())
        return self._lang_codes[i], float(probs[i])

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None, final: bool) -> Transcript:
        if self.model is None:
            raise RuntimeError("model not loaded")
        if language and self._lang_codes and language not in self._lang_codes:
            raise UnsupportedLanguage(language)
        try:
            with torch.inference_mode():
                feats = self._features(audio)
                prob = None
                if not language:
                    language, prob = self._detect_language(feats)
                kwargs = dict(task="transcribe", language=language, num_beams=self.final_beams if final else 1,
                              do_sample=False, return_dict_in_generate=True)
                prompt_len = 0
                if prompt:
                    prompt_ids = self.processor.get_prompt_ids(prompt, return_tensors="pt").to(self.device)
                    prompt_ids = prompt_ids[-120:]  # keep the prompt short; it shares the 448-token budget
                    prompt_len = int(prompt_ids.shape[-1])
                    kwargs["prompt_ids"] = prompt_ids
                kwargs["max_new_tokens"] = max(32, min(224, 440 - prompt_len - 4))
                out = self.model.generate(feats, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, torch.cuda.OutOfMemoryError) or is_oom_error(exc):
                raise OutOfMemory(str(exc)) from exc
            if self.device == "cuda" and is_fatal_cuda_error(exc):
                raise GpuFailure(str(exc)) from exc
            raise
        text = self.processor.batch_decode(out.sequences, skip_special_tokens=True)[0].strip()
        if prompt and text.startswith(prompt.strip()):
            text = text[len(prompt.strip()):].strip()
        # Beam search reports the length-normalised log probability of the
        # chosen sequence, which is Whisper's avg_logprob.
        seq_scores = getattr(out, "sequences_scores", None)
        avg_logprob = float(seq_scores[0]) if seq_scores is not None else 0.0
        return Transcript(
            text=text,
            language=language,
            language_probability=prob if prob is not None else 1.0,
            no_speech_prob=0.0,
            avg_logprob=avg_logprob,
            compression_ratio=zlib_compression_ratio(text) if text else 1.0,
        )
