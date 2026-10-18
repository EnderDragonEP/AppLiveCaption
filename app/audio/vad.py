"""Streaming Silero VAD using the ONNX model bundled with faster-whisper.

faster-whisper's own ``SileroVADModel`` resets its recurrent state on every
call, which suits whole files. Live audio needs the state (and the 64-sample
context) carried from one call to the next, so the session is driven here
directly. Everything runs offline on the CPU.
"""

from __future__ import annotations

import os
import threading

import numpy as np

FRAME = 512  # samples per VAD frame at 16 kHz (32 ms)
CONTEXT = 64

_session = None
_session_lock = threading.Lock()


def _model_path() -> str:
    from faster_whisper.utils import get_assets_path

    return os.path.join(get_assets_path(), "silero_vad_v6.onnx")


def get_session():
    """One shared ONNX session (run() is thread-safe; the recurrent state lives in StreamingVAD).

    Importing onnxruntime holds the GIL for about a second, so the app calls
    this once at start-up behind the splash screen.
    """
    global _session
    with _session_lock:
        if _session is None:
            import onnxruntime

            opts = onnxruntime.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            opts.log_severity_level = 4
            _session = onnxruntime.InferenceSession(_model_path(), providers=["CPUExecutionProvider"], sess_options=opts)
        return _session


class StreamingVAD:
    def __init__(self):
        self._session = get_session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), np.float32)
        self._c = np.zeros((1, 1, 128), np.float32)
        self._context = np.zeros(CONTEXT, np.float32)
        self._pending = np.zeros(0, np.float32)

    def process(self, samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Feed audio; return (frames, probabilities) for every complete frame.

        ``frames`` has shape (n, FRAME); leftover samples are kept for the
        next call.
        """
        if samples.size:
            self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        n = self._pending.size // FRAME
        if n == 0:
            return np.zeros((0, FRAME), np.float32), np.zeros(0, np.float32)
        frames = self._pending[: n * FRAME].reshape(n, FRAME)
        self._pending = self._pending[n * FRAME:]
        # Each frame is prefixed with the last 64 samples of the previous one.
        prev = np.concatenate([self._context[None, :], frames[:-1, -CONTEXT:]], axis=0)
        batch = np.concatenate([prev, frames], axis=1)
        probs, self._h, self._c = self._session.run(None, {"input": batch, "h": self._h, "c": self._c})
        self._context = frames[-1, -CONTEXT:].copy()
        return frames, np.asarray(probs, np.float32).reshape(-1)
