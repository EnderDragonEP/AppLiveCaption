"""Streaming captions on top of a non-streaming Whisper-family model.

Audio goes through Silero VAD. While someone speaks, the current utterance is
re-transcribed every ``partial_interval`` seconds and shown as a *partial*
caption; LocalAgreement keeps the start of the partial fixed once two
consecutive hypotheses agree on it, so it doesn't flicker. When the VAD sees
the end of speech, or the utterance gets too long, a more accurate *final*
transcription is emitted and a new utterance starts. The previous final text is
passed to the model as the prompt.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from dataclasses import dataclass, field

import numpy as np

from ..audio.resample import TARGET_RATE
from ..audio.vad import FRAME, StreamingVAD
from .backend import ASRBackend, Transcript
from .filters import HallucinationFilter, normalize
from .languages import CHINESE_CODES
from .textconv import CHINESE_TRADITIONAL, ChineseConverter

log = logging.getLogger(__name__)

SR = TARGET_RATE


@dataclass
class StreamingConfig:
    partial_interval: float = 0.6  # seconds between partial updates
    min_silence: float = 0.6  # silence that ends an utterance
    max_utterance: float = 18.0  # force a final caption after this long
    speech_threshold: float = 0.5
    silence_threshold: float = 0.35
    pre_roll: float = 0.3  # audio kept from before speech started
    post_roll: float = 0.2  # trailing silence kept at the end of an utterance
    min_speech: float = 0.25  # shorter blips are ignored
    max_lag: float = 4.0  # drop audio when inference falls this far behind
    prompt_chars: int = 200


@dataclass
class CaptionEvent:
    kind: str  # "partial" | "final" | "dropped"
    text: str = ""
    stable: str = ""  # partial only: the part that will not change any more
    start: float = 0.0  # seconds since the session started
    end: float = 0.0
    latency: float = 0.0  # seconds from capture to display
    language: str | None = None
    decode_time: float = 0.0


# ---------------------------------------------------------------------------
# LocalAgreement
# ---------------------------------------------------------------------------
_TOKEN = re.compile(
    r"\s*(?:[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯＀-￯　-〿]"
    r"|[^\s぀-ヿ㐀-䶿一-鿿豈-﫿가-힯＀-￯　-〿]+)"
)
_STRIP = re.compile(r"[\s,.!?;:，。！？；：、\"'“”‘’()（）]+")


def tokenize(text: str) -> list[str]:
    """Words for space-separated scripts, single characters for CJK (leading spaces kept)."""
    return _TOKEN.findall(text)


def _key(tok: str) -> str:
    return _STRIP.sub("", tok).lower()


class PartialStabilizer:
    """LocalAgreement-2: a token is committed once two consecutive hypotheses agree on it."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.committed: list[str] = []
        self._prev_tail: list[str] = []

    def update(self, text: str) -> tuple[str, str]:
        tokens = tokenize(text)
        keys = [_key(t) for t in tokens]
        ckeys = [_key(t) for t in self.committed]
        if keys[: len(ckeys)] == ckeys:
            tail = tokens[len(ckeys):]
        else:
            # The model revised words that were already committed. Keep the
            # committed text and continue after its best match in the new hypothesis.
            sm = difflib.SequenceMatcher(None, ckeys, keys, autojunk=False)
            end = 0
            for block in sm.get_matching_blocks():
                if block.size:
                    end = block.b + block.size
            tail = tokens[end:]
        prev_keys = [_key(t) for t in self._prev_tail]
        agreed = 0
        for a, b in zip(prev_keys, (_key(t) for t in tail)):
            if a != b:
                break
            agreed += 1
        self.committed.extend(tail[:agreed])
        self._prev_tail = tail[agreed:]
        return "".join(self.committed).strip(), "".join(tail[agreed:])


# ---------------------------------------------------------------------------
# Streaming transcriber
# ---------------------------------------------------------------------------
@dataclass
class _Utterance:
    start_pos: int  # sample position of the first sample
    frames: list[np.ndarray] = field(default_factory=list)
    probs: list[float] = field(default_factory=list)
    silence_samples: int = 0
    language: str | None = None
    decoded_samples: int = 0  # length at the last partial decode

    @property
    def samples(self) -> int:
        return len(self.frames) * FRAME

    def audio(self, n_frames: int | None = None) -> np.ndarray:
        frames = self.frames if n_frames is None else self.frames[:n_frames]
        return np.concatenate(frames) if frames else np.zeros(0, np.float32)


class StreamingTranscriber:
    def __init__(self, backend: ASRBackend, language: str | None = None, chinese_output: str = CHINESE_TRADITIONAL,
                 base_prompt: str = "", config: StreamingConfig | None = None):
        self.backend = backend
        self.language = language or None
        self.base_prompt = base_prompt
        self.cfg = config or StreamingConfig()
        self.vad = StreamingVAD()
        self.filter = HallucinationFilter()
        self.converter = ChineseConverter(chinese_output)  # (its .output can change while running)
        self.stabilizer = PartialStabilizer()
        self.pos = 0  # samples consumed since the session started (incl. dropped)
        self._pre_roll: list[np.ndarray] = []
        self._utt: _Utterance | None = None
        self._last_partial_at = 0.0
        self._partial_cost = 0.0
        self._clock: tuple[float, int] | None = None  # (capture time, sample position) of the newest audio
        self.prompt_history = ""
        self._prompt_language: str | None = None
        self.last_latency = 0.0

    # ------------------------------------------------------------------
    def process(self, samples: np.ndarray, t_end: float | None) -> list[CaptionEvent]:
        """Feed newly captured audio; returns the caption events it produced."""
        events: list[CaptionEvent] = []
        if samples.size == 0:
            return events
        lag_limit = int(self.cfg.max_lag * SR)
        if samples.size > lag_limit:
            # Inference can't keep up: finish what we have and skip ahead.
            keep = SR  # keep the newest second
            dropped = samples.size - keep
            events += self._finalize(force=True)
            self.vad.reset()
            self._pre_roll.clear()
            self.pos += dropped
            samples = samples[-keep:]
            events.append(CaptionEvent("dropped", start=self.pos / SR, end=dropped / SR))
            log.warning("ASR fell behind; dropped %.1f s of audio", dropped / SR)
        if t_end is not None:
            self._clock = (t_end, self.pos + samples.size + self.vad._pending.size)

        frames, probs = self.vad.process(samples)
        for frame, p in zip(frames, probs):
            events += self._on_frame(frame, float(p))
            self.pos += FRAME
        # Leftover samples (< one frame) stay inside the VAD until next time.

        utt = self._utt
        if utt is not None and utt.samples >= 0.5 * SR:
            now = time.monotonic()
            interval = max(self.cfg.partial_interval, self._partial_cost * 1.5)
            if now - self._last_partial_at >= interval and utt.samples - utt.decoded_samples >= 0.25 * SR:
                ev = self._partial()
                if ev:
                    events.append(ev)
        return events

    def flush(self) -> list[CaptionEvent]:
        """Finish the current utterance (e.g. when captioning stops)."""
        return self._finalize(force=True)

    # ------------------------------------------------------------------
    def _on_frame(self, frame: np.ndarray, p: float) -> list[CaptionEvent]:
        cfg = self.cfg
        utt = self._utt
        if utt is None:
            self._pre_roll.append(frame)
            max_pre = max(1, int(cfg.pre_roll * SR / FRAME))
            if len(self._pre_roll) > max_pre:
                self._pre_roll.pop(0)
            if p >= cfg.speech_threshold:
                start = self.pos + FRAME - len(self._pre_roll) * FRAME
                self._utt = _Utterance(start_pos=start, frames=list(self._pre_roll),
                                       probs=[0.0] * (len(self._pre_roll) - 1) + [p])
                self._pre_roll.clear()
                self.stabilizer.reset()
            return []

        utt.frames.append(frame)
        utt.probs.append(p)
        if p < cfg.silence_threshold:
            utt.silence_samples += FRAME
        elif p >= cfg.speech_threshold:
            utt.silence_samples = 0
        if utt.silence_samples >= cfg.min_silence * SR:
            return self._finalize()
        if utt.samples >= cfg.max_utterance * SR:
            return self._split_long_utterance()
        return []

    def _split_long_utterance(self) -> list[CaptionEvent]:
        """Cut at the quietest point of the last 3 s so words aren't split."""
        utt = self._utt
        assert utt is not None
        n = len(utt.frames)
        window = int(3 * SR / FRAME)
        lo = max(1, n - window)
        hi = max(lo + 1, n - int(0.3 * SR / FRAME))
        cut = lo + int(np.argmin(utt.probs[lo:hi]))
        rest_frames, rest_probs = utt.frames[cut:], utt.probs[cut:]
        utt.frames, utt.probs = utt.frames[:cut], utt.probs[:cut]
        events = self._finalize(force=True)
        self._utt = _Utterance(start_pos=utt.start_pos + cut * FRAME, frames=rest_frames, probs=rest_probs,
                               language=utt.language)
        self.stabilizer.reset()
        return events

    # ------------------------------------------------------------------
    def _prompt(self, language: str | None) -> str | None:
        hist = self.prompt_history[-self.cfg.prompt_chars:]
        # A prompt in another language pushes Whisper to transcribe (or just
        # repeat) it in that language.
        if language and self._prompt_language and language != self._prompt_language:
            hist = ""
        prompt = (self.base_prompt + " " + hist).strip() if self.base_prompt else hist
        return prompt or None

    def _is_echo(self, text: str, prompt: str | None) -> bool:
        """Whisper sometimes outputs the prompt instead of the audio."""
        if not prompt or not text:
            return False
        a, b = normalize(text), normalize(prompt)
        if len(a) < 4:
            return False
        return a in b or difflib.SequenceMatcher(None, a, b[-len(a) * 2:], autojunk=False).ratio() > 0.8

    def _latency_for(self, end_pos: int) -> float:
        if not self._clock:
            return 0.0
        t_end, pos_end = self._clock
        captured = t_end - (pos_end - end_pos) / SR
        return max(0.0, time.monotonic() - captured)

    def _decode(self, audio: np.ndarray, language: str | None, final: bool) -> tuple[Transcript, float]:
        t0 = time.monotonic()
        prompt = self._prompt(language)
        tr = self.backend.transcribe(audio, language, prompt, final)
        if self._is_echo(tr.text, prompt):
            log.debug("prompt echo detected, retrying without prompt: %r", tr.text)
            tr = self.backend.transcribe(audio, language, self.base_prompt or None, final)
        return tr, time.monotonic() - t0

    def _postprocess(self, text: str, language: str | None) -> str:
        if text and (language in CHINESE_CODES or language is None):
            text = self.converter.convert(text)
        return text

    def _partial(self) -> CaptionEvent | None:
        utt = self._utt
        assert utt is not None
        audio = utt.audio()
        lang = self.language or utt.language
        tr, cost = self._decode(audio, lang, final=False)
        self._partial_cost = cost
        self._last_partial_at = time.monotonic()
        utt.decoded_samples = audio.size
        if not self.language and utt.language is None and audio.size >= SR and (tr.language_probability or 0) >= 0.5:
            utt.language = tr.language  # keep partials of this utterance in one language
        text = self.filter.clean(tr, audio.size / SR, final=False)
        if not text:
            return None
        text = self._postprocess(text, self.language or tr.language)
        stable, unstable = self.stabilizer.update(text)
        end_pos = utt.start_pos + audio.size
        self.last_latency = self._latency_for(end_pos)
        full = (stable + unstable) if stable else unstable
        return CaptionEvent("partial", text=full.strip(), stable=stable, start=utt.start_pos / SR, end=end_pos / SR,
                            latency=self.last_latency, language=self.language or tr.language, decode_time=cost)

    def _finalize(self, force: bool = False) -> list[CaptionEvent]:
        utt = self._utt
        if utt is None:
            return []
        self._utt = None
        self.stabilizer.reset()
        # Trim trailing silence, keeping a little.
        n = len(utt.frames)
        if not force:
            keep_tail = int(self.cfg.post_roll * SR / FRAME)
            silent_frames = utt.silence_samples // FRAME
            n = max(1, n - max(0, silent_frames - keep_tail))
        speech_frames = sum(1 for p in utt.probs[:n] if p >= self.cfg.speech_threshold)
        if speech_frames * FRAME < self.cfg.min_speech * SR:
            # Too short to be speech; clear any partial that was shown for it.
            return [CaptionEvent("final", text="")] if utt.decoded_samples else []
        audio = utt.audio(n)
        tr, cost = self._decode(audio, self.language or utt.language, final=True)
        text = self.filter.clean(tr, audio.size / SR, final=True)
        end_pos = utt.start_pos + audio.size
        latency = self._latency_for(end_pos)
        self.last_latency = latency
        if not text:
            # Still emit an empty final so the UI clears the partial line.
            return [CaptionEvent("final", text="", start=utt.start_pos / SR, end=end_pos / SR, latency=latency)]
        language = self.language or tr.language
        text = self._postprocess(text, language)
        # The previous final caption becomes the prompt for the next utterance.
        self.prompt_history = text
        self._prompt_language = language
        return [CaptionEvent("final", text=text, start=utt.start_pos / SR, end=end_pos / SR,
                             latency=latency, language=language, decode_time=cost)]
