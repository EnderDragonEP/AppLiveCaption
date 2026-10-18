"""Thread-safe FIFO of float32 audio samples."""

from __future__ import annotations

import collections
import threading
import time

import numpy as np


class AudioBuffer:
    """A FIFO of mono float32 samples with drop-oldest semantics.

    Producers call :meth:`push` from capture threads; one consumer calls
    :meth:`pull`. ``last_push`` records the monotonic time of the newest data
    so callers can tell a live source from one that has gone quiet.
    """

    def __init__(self, max_samples: int | None = None):
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._size = 0
        self._lock = threading.Lock()
        self._max = max_samples
        self.last_push = 0.0
        self.dropped = 0  # total samples dropped because of overflow

    def __len__(self) -> int:
        return self._size

    def push(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        with self._lock:
            self._chunks.append(samples)
            self._size += samples.size
            self.last_push = time.monotonic()
            if self._max is not None and self._size > self._max:
                self._drop_locked(self._size - self._max)

    def pull(self, n: int | None = None) -> np.ndarray:
        """Remove and return up to ``n`` samples (all of them if ``n`` is None)."""
        with self._lock:
            if n is None or n >= self._size:
                out = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
                self._chunks.clear()
                self._size = 0
                return out
            parts = []
            need = n
            while need > 0 and self._chunks:
                head = self._chunks[0]
                if head.size <= need:
                    parts.append(self._chunks.popleft())
                    need -= head.size
                else:
                    parts.append(head[:need])
                    self._chunks[0] = head[need:]
                    need = 0
            self._size -= n - need
            return np.concatenate(parts) if parts else np.zeros(0, np.float32)

    def drop_oldest(self, n: int) -> None:
        with self._lock:
            self._drop_locked(n)

    def _drop_locked(self, n: int) -> None:
        n = min(n, self._size)
        self.dropped += n
        self._size -= n
        while n > 0 and self._chunks:
            head = self._chunks[0]
            if head.size <= n:
                self._chunks.popleft()
                n -= head.size
            else:
                self._chunks[0] = head[n:]
                n = 0

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._size = 0
