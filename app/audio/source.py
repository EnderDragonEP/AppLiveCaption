"""Base class for audio sources.

Every source captures on its own thread, converts what it gets to 16 kHz mono
float32 and pushes it into ``self.buffer``. The mixer pulls from there.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable

import numpy as np

from .buffer import AudioBuffer
from .resample import TARGET_RATE, StreamResampler, rms_dbfs

log = logging.getLogger(__name__)


class SourceState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"  # e.g. the selected app is not running
    ERROR = "error"


StatusCallback = Callable[["AudioSource", SourceState, str], None]


class AudioSource:
    kind = "base"

    def __init__(self, key: str, label: str, on_status: StatusCallback | None = None):
        self.key = key
        self.label = label
        self.on_status = on_status
        # Keep at most 10 s per source; the mixer drains it every few ms.
        self.buffer = AudioBuffer(max_samples=TARGET_RATE * 10)
        self.state = SourceState.STOPPED
        self.message = ""
        self.level_db = -120.0
        self._resampler: StreamResampler | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._set_state(SourceState.STARTING)
        self._thread = threading.Thread(target=self._thread_main, name=f"capture-{self.key}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None
        if self.state != SourceState.ERROR:
            self._set_state(SourceState.STOPPED)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _thread_main(self) -> None:
        try:
            self.run()
        except Exception as exc:  # noqa: BLE001 - reported through status
            log.exception("Audio source %s failed", self.key)
            self._set_state(SourceState.ERROR, str(exc))

    def run(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers for subclasses ----------------------------------------
    def _set_state(self, state: SourceState, message: str = "") -> None:
        changed = state != self.state or message != self.message
        self.state = state
        self.message = message
        if changed and self.on_status:
            try:
                self.on_status(self, state, message)
            except Exception:  # noqa: BLE001
                log.exception("status callback failed")

    def _emit(self, frames: np.ndarray, rate: int) -> None:
        """Resample captured frames to 16 kHz mono and queue them."""
        if self._resampler is None:
            self._resampler = StreamResampler(rate)
        out = self._resampler.process(frames, rate)
        if out.size:
            self.buffer.push(out)
            # Exponential smoothing so the console/UI meter doesn't jump.
            db = rms_dbfs(out)
            self.level_db = db if db > self.level_db else self.level_db * 0.7 + db * 0.3

    def _sleep(self, seconds: float) -> bool:
        """Sleep unless asked to stop. Returns True if stopping."""
        return self._stop.wait(seconds)

    @property
    def idle_seconds(self) -> float:
        last = self.buffer.last_push
        return time.monotonic() - last if last else float("inf")
