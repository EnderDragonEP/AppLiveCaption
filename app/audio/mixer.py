"""Mixes several 16 kHz mono sources into one stream.

The mixer runs on a wall clock: every tick it produces exactly the number of
samples that elapsed, taking what each source has buffered and padding with
silence otherwise. WASAPI loopback endpoints deliver nothing while nothing
plays, so without a clock a paused source would stall the stream and the
voice activity detector would never see the end of an utterance.
"""

from __future__ import annotations

import collections
import threading
import time

import numpy as np

from .resample import TARGET_RATE, rms_dbfs
from .source import AudioSource


class Limiter:
    """Peak limiter with instant attack and smooth release, applied in 10 ms blocks."""

    def __init__(self, threshold: float = 0.89, release_s: float = 0.25, rate: int = TARGET_RATE):
        self.threshold = threshold
        self.block = rate // 100
        self.release = 1.0 - np.exp(-self.block / (release_s * rate))
        self.gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        for start in range(0, x.size, self.block):
            seg = x[start:start + self.block]
            peak = float(np.max(np.abs(seg))) if seg.size else 0.0
            target = min(1.0, self.threshold / peak) if peak > 0 else 1.0
            prev = self.gain
            self.gain = target if target < prev else prev + (target - prev) * self.release
            # Ramp across the block to avoid zipper noise.
            out[start:start + seg.size] = seg * np.linspace(prev, self.gain, seg.size, dtype=np.float32)
        return np.clip(out, -1.0, 1.0, out=out)


class TimedQueue:
    """FIFO of (samples, capture_end_time) chunks with a size cap (drops oldest)."""

    def __init__(self, max_seconds: float = 30.0, rate: int = TARGET_RATE):
        self._q: collections.deque[tuple[np.ndarray, float]] = collections.deque()
        self._size = 0
        self._max = int(max_seconds * rate)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self.dropped = 0

    @property
    def samples(self) -> int:
        return self._size

    def push(self, chunk: np.ndarray, t_end: float) -> None:
        with self._cond:
            self._q.append((chunk, t_end))
            self._size += chunk.size
            while self._size > self._max and self._q:
                old, _ = self._q.popleft()
                self._size -= old.size
                self.dropped += old.size
            self._cond.notify_all()

    def pop_all(self, timeout: float = 0.1) -> tuple[np.ndarray, float | None]:
        """Wait up to ``timeout`` for data; return (samples, capture time of the last sample)."""
        with self._cond:
            if not self._q:
                self._cond.wait(timeout)
            if not self._q:
                return np.zeros(0, np.float32), None
            chunks = [c for c, _ in self._q]
            t_end = self._q[-1][1]
            self._q.clear()
            self._size = 0
        return np.concatenate(chunks), t_end

    def clear(self) -> None:
        with self._lock:
            self._q.clear()
            self._size = 0


class Mixer:
    def __init__(self, margin: float = 0.15, tick: float = 0.02, max_backlog: float = 0.6, rate: int = TARGET_RATE):
        self.rate = rate
        self.margin = margin  # absorbs delivery jitter of the sources
        self.tick = tick
        self.max_backlog = max_backlog
        self.output = TimedQueue(rate=rate)
        self.limiter = Limiter(rate=rate)
        self.level_db = -120.0
        self._sources: dict[str, AudioSource] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- sources -----------------------------------------------------------
    def add(self, source: AudioSource) -> None:
        with self._lock:
            self._sources[source.key] = source

    def remove(self, key: str) -> None:
        with self._lock:
            self._sources.pop(key, None)

    def sources(self) -> list[AudioSource]:
        with self._lock:
            return list(self._sources.values())

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mixer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
        self._thread = None

    def _run(self) -> None:
        rate = self.rate
        t0 = time.monotonic()
        emitted = 0
        backlog_cap = int(self.max_backlog * rate)
        while not self._stop.wait(self.tick):
            now = time.monotonic()
            due = int((now - t0 - self.margin) * rate) - emitted
            if due <= 0:
                continue
            if due > rate:
                # The process was stalled (e.g. the PC slept): resync instead of
                # emitting a burst of silence.
                emitted += due - self.tick_samples
                due = self.tick_samples
            mix = np.zeros(due, np.float32)
            for src in self.sources():
                buf = src.buffer
                excess = len(buf) - (due + backlog_cap)
                if excess > 0:
                    buf.drop_oldest(excess)  # source clock runs faster than ours
                chunk = buf.pull(due)
                if chunk.size:
                    mix[:chunk.size] += chunk
            mix = self.limiter.process(mix)
            emitted += due
            db = rms_dbfs(mix)
            self.level_db = db if db > self.level_db else self.level_db * 0.7 + db * 0.3
            self.output.push(mix, t0 + emitted / rate)

    @property
    def tick_samples(self) -> int:
        return int(self.tick * self.rate)
