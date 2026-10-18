"""Conversion of captured audio to 16 kHz mono float32."""

from __future__ import annotations

import numpy as np
import soxr

TARGET_RATE = 16000


def to_mono(frames: np.ndarray) -> np.ndarray:
    """Downmix an (n, channels) or (n,) array to mono float32."""
    if frames.ndim == 2:
        frames = frames.mean(axis=1) if frames.shape[1] > 1 else frames[:, 0]
    return np.ascontiguousarray(frames, dtype=np.float32)


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


class StreamResampler:
    """Streaming resampler from any input rate to 16 kHz mono.

    Keeps filter state between calls so block boundaries don't click. If the
    input rate changes (a device was reopened with another format) the
    resampler is rebuilt.
    """

    def __init__(self, in_rate: int, out_rate: int = TARGET_RATE):
        self.out_rate = out_rate
        self.in_rate = 0
        self._rs = None
        self._configure(in_rate)

    def _configure(self, in_rate: int) -> None:
        self.in_rate = int(in_rate)
        if self.in_rate == self.out_rate:
            self._rs = None
        else:
            self._rs = soxr.ResampleStream(self.in_rate, self.out_rate, 1, dtype="float32", quality="MQ")

    def process(self, frames: np.ndarray, in_rate: int | None = None) -> np.ndarray:
        if in_rate is not None and int(in_rate) != self.in_rate:
            self._configure(in_rate)
        mono = to_mono(frames)
        if self._rs is None:
            return mono
        return self._rs.resample_chunk(mono)


def rms_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return 20.0 * np.log10(max(rms, 1e-6))
