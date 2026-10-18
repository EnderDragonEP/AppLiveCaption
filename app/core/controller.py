"""Connects audio capture, the ASR model and the UI.

All capture and inference work happens on ``AsrWorker`` (a QThread) or the
capture threads; the GUI thread only sends commands and receives signals.
"""

from __future__ import annotations

import gc
import logging
import queue
import sys
import time

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..asr.backend import ASRBackend, GpuFailure, ModelLoadError, OutOfMemory, UnsupportedLanguage
from ..asr.factory import BackendUnavailable, create_backend
from ..asr.hardware import gpu_info, realtime_risk, resolve_device
from ..asr.streaming import StreamingTranscriber
from ..audio.capture import CaptureManager, CaptureSelection
from ..models.catalog import BACKEND_TRANSFORMERS, ModelInfo, get_model
from ..models.storage import is_downloaded, model_path, verify
from .history import HistoryLine, HistorySession, HistoryStore

log = logging.getLogger(__name__)

# Run states
IDLE = "idle"
RUNNING = "running"
PAUSED = "paused"  # captioning was running and is suspended while the model changes

# Model states
MODEL_NONE = "none"
MODEL_LOADING = "loading"
MODEL_READY = "ready"
MODEL_FAILED = "failed"


class AsrWorker(QThread):
    partial = Signal(str, str)  # full text, stable prefix
    final = Signal(str, float, float)  # text, start, end (seconds since session start)
    latency = Signal(float)
    modelState = Signal(str, str)  # state, model id
    deviceChanged = Signal(str, str)  # device ("cuda"/"cpu"), compute type
    runState = Signal(str)
    error = Signal(str, str)  # kind, detail
    notice = Signal(str, dict)  # capture events and other non-fatal information
    languagesChanged = Signal(list)  # language codes the loaded model accepts
    levels = Signal(list)  # [(label, kind, dB, state)] for the active sources

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cmds: queue.Queue = queue.Queue()
        self.backend: ASRBackend | None = None
        self.model: ModelInfo | None = None
        self.device = "cpu"
        self.capture = CaptureManager(on_event=lambda name, payload: self.notice.emit(name, payload))
        self.transcriber: StreamingTranscriber | None = None
        self.selection = CaptureSelection()
        self.language: str | None = None
        self.opencc = True
        self.running = False
        self._last_levels = 0.0
        self._last_drop_notice = 0.0
        self._last_error = 0.0

    # -- commands (called from the GUI thread) ----------------------------
    def post(self, cmd: str, **kwargs) -> None:
        self._cmds.put((cmd, kwargs))

    # -- thread body --------------------------------------------------------
    def run(self) -> None:
        while True:
            try:
                cmd, kw = self._cmds.get(timeout=0.0 if self.running else 0.2)
            except queue.Empty:
                cmd = None
            if cmd == "quit":
                self._stop()
                self._unload()
                return
            if cmd:
                try:
                    getattr(self, f"_cmd_{cmd}")(**kw)
                except Exception as exc:  # noqa: BLE001
                    log.exception("worker command %s failed", cmd)
                    self.error.emit("internal", str(exc))
                continue
            if self.running:
                self._step()

    def _step(self) -> None:
        assert self.transcriber is not None
        audio, t_end = self.capture.output.pop_all(timeout=0.1)
        try:
            events = self.transcriber.process(audio, t_end)
        except OutOfMemory:
            self._move_to_cpu("gpu_oom_cpu_fallback")
            return
        except GpuFailure:
            log.exception("GPU failure")
            self._move_to_cpu("gpu_failed_cpu_fallback")
            return
        except UnsupportedLanguage as exc:
            self.error.emit("language_unsupported", str(exc))
            self.language = None
            self.transcriber.language = None
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("transcription failed")
            now = time.monotonic()
            if now - self._last_error > 30:
                self._last_error = now
                self.error.emit("transcribe_failed", str(exc))
            return
        self._emit_events(events)
        now = time.monotonic()
        if now - self._last_levels > 0.25:
            self._last_levels = now
            lv = [(s.label, s.kind, db, s.state.value) for s, db in self.capture.levels()]
            self.levels.emit(lv)

    def _emit_events(self, events) -> None:
        for ev in events:
            if ev.kind == "partial":
                self.partial.emit(ev.text, ev.stable)
                self.latency.emit(ev.latency)
            elif ev.kind == "final":
                self.final.emit(ev.text, ev.start, ev.end)
                if ev.text:
                    self.latency.emit(ev.latency)
            elif ev.kind == "dropped":
                now = time.monotonic()
                if now - self._last_drop_notice > 30:
                    self._last_drop_notice = now
                    self.notice.emit("falling_behind", {"seconds": ev.end})

    # -- model --------------------------------------------------------------
    def _cmd_load(self, model_id: str, device_pref: str) -> None:
        info = get_model(model_id)
        if info is None:
            self.error.emit("unknown_model", model_id)
            return
        was_running = self.running
        if was_running:
            self._pause()
        self._unload()
        self.modelState.emit(MODEL_LOADING, model_id)
        if self._load(info, device_pref):
            self.modelState.emit(MODEL_READY, model_id)
            if was_running:
                self._start()
        else:
            self.modelState.emit(MODEL_FAILED, model_id)
            if was_running:
                self.runState.emit(IDLE)

    def _load(self, info: ModelInfo, device_pref: str) -> bool:
        problem = verify(info) if is_downloaded(info) else "missing"
        if problem:
            self.error.emit("model_corrupt" if problem != "missing" else "model_missing", f"{info.name}: {problem}")
            return False
        device = self._pick_device(info, device_pref)
        risk = realtime_risk(info.tier, device, gpu_info() if device == "cuda" else None)
        if risk:
            self.notice.emit("slow_model", {"model": info.name, "risk": risk, "device": device})
        try:
            backend = create_backend(info)
        except BackendUnavailable as exc:
            self.error.emit("backend_missing", str(exc))
            return False
        try:
            backend.load(str(model_path(info)), device)
        except OutOfMemory:
            if device != "cuda":
                self.error.emit("oom", info.name)
                return False
            self._free_gpu()
            self.notice.emit("gpu_oom_cpu_fallback", {"model": info.name})
            device = "cpu"
            try:
                backend.load(str(model_path(info)), device)
            except Exception as exc:  # noqa: BLE001
                self.error.emit("model_corrupt" if isinstance(exc, ModelLoadError) else "oom", str(exc))
                return False
        except ModelLoadError as exc:
            self.error.emit("model_corrupt", f"{info.name}: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("model load failed")
            self.error.emit("model_corrupt", f"{info.name}: {exc}")
            return False
        self._warm_up(backend)
        self.backend = backend
        self.model = info
        self.device = device
        self.deviceChanged.emit(device, backend.compute_type)
        langs = backend.supported_languages()
        allowed = [c for c in info.languages if langs is None or c in langs]
        self.languagesChanged.emit(allowed)
        if self.language and self.language not in allowed:
            self.error.emit("language_unsupported", self.language)
            self.language = None
        return True

    @staticmethod
    def _warm_up(backend: ASRBackend) -> None:
        """Run one short decode while the loading indicator is still showing.

        The first inference initialises CUDA kernels and cuDNN, which can take a
        second or two; doing it here keeps the first caption fast.
        """
        langs = backend.supported_languages()
        lang = "en" if langs is None or "en" in langs else None
        try:
            backend.transcribe(np.zeros(16000, np.float32), lang, None, final=False)
        except Exception:  # noqa: BLE001 - a failed warm-up shows up again on real audio
            log.debug("warm-up decode failed", exc_info=True)

    def _pick_device(self, info: ModelInfo, pref: str) -> str:
        if info.backend == BACKEND_TRANSFORMERS:
            if pref == "cpu":
                return "cpu"
            try:
                import torch

                return "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:  # noqa: BLE001
                return "cpu"
        return resolve_device(pref)

    def _unload(self) -> None:
        if self.backend is not None:
            was_cuda = self.device == "cuda"
            try:
                self.backend.unload()
            except Exception:  # noqa: BLE001
                log.exception("unload failed")
            self.backend = None
            self.transcriber = None
            if was_cuda:
                self._free_gpu()
        self.model = None

    @staticmethod
    def _free_gpu() -> None:
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass

    def _move_to_cpu(self, notice: str) -> None:
        """The GPU ran out of memory or failed mid-session: reload the model on the CPU."""
        info = self.model
        self.notice.emit(notice, {"model": info.name if info else ""})
        if info is None:
            return
        self._cmd_load(info.id, "cpu")

    # -- capture --------------------------------------------------------------
    def _cmd_start(self, selection: CaptureSelection, language: str | None, opencc: bool) -> None:
        self.selection = selection
        self.language = language
        self.opencc = opencc
        if self.backend is None:
            self.error.emit("no_model", "")
            return
        self._start()

    def _start(self) -> None:
        assert self.backend is not None and self.model is not None
        self.transcriber = StreamingTranscriber(self.backend, language=self.language,
                                                convert_chinese=self.opencc, base_prompt=self.model.initial_prompt)
        try:
            self.capture.start(self.selection)
        except Exception as exc:  # noqa: BLE001
            log.exception("capture start failed")
            self.error.emit("capture_failed", str(exc))
            return
        self.running = True
        self.runState.emit(RUNNING)

    def _pause(self) -> None:
        self._flush()
        self.capture.stop()
        self.running = False
        self.runState.emit(PAUSED)

    def _stop(self) -> None:
        if self.running:
            self._flush()
            self.capture.stop()
        self.running = False
        self.runState.emit(IDLE)

    def _cmd_stop(self) -> None:
        self._stop()

    def _flush(self) -> None:
        if self.transcriber is None:
            return
        try:
            self._emit_events(self.transcriber.flush())
        except Exception:  # noqa: BLE001
            log.exception("flush failed")

    def _cmd_set_selection(self, selection: CaptureSelection) -> None:
        self.selection = selection
        if self.running:
            self._flush()
            self.capture.start(selection)

    def _cmd_set_language(self, language: str | None) -> None:
        self.language = language
        if self.transcriber is not None:
            self._flush()
            self.transcriber.language = self.language

    def _cmd_set_opencc(self, enabled: bool) -> None:
        self.opencc = enabled
        if self.transcriber is not None:
            self.transcriber.convert_chinese = enabled


class CaptionController(QObject):
    """GUI-side facade over the worker; also records history."""

    partial = Signal(str, str)
    final = Signal(str, float, float)
    latency = Signal(float)
    modelState = Signal(str, str)
    deviceChanged = Signal(str, str)
    runState = Signal(str)
    error = Signal(str, str)
    notice = Signal(str, dict)
    languagesChanged = Signal(list)
    levels = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = AsrWorker()
        self.history = HistoryStore()
        self.session: HistorySession | None = None
        self.model_id: str | None = None
        self.model_state = MODEL_NONE
        self.run_state = IDLE
        self.device = ""
        self.compute_type = ""
        self.selection = CaptureSelection()
        self.source_label = ""
        self._dirty = False

        w = self.worker
        w.partial.connect(self.partial)
        w.final.connect(self._on_final)
        w.latency.connect(self.latency)
        w.modelState.connect(self._on_model_state)
        w.deviceChanged.connect(self._on_device)
        w.runState.connect(self._on_run_state)
        w.error.connect(self.error)
        w.notice.connect(self.notice)
        w.languagesChanged.connect(self.languagesChanged)
        w.levels.connect(self.levels)
        w.start()

        self._save_timer = QTimer(self)
        self._save_timer.setInterval(3000)
        self._save_timer.timeout.connect(self._save_history)
        self._save_timer.start()

    # -- commands -------------------------------------------------------------
    def load_model(self, model_id: str, device_pref: str) -> None:
        self.model_id = model_id
        self.worker.post("load", model_id=model_id, device_pref=device_pref)

    def start(self, selection: CaptureSelection, language: str | None, opencc: bool, source_label: str) -> None:
        self.selection = selection
        self.source_label = source_label
        self.worker.post("start", selection=selection, language=language, opencc=opencc)

    def stop(self) -> None:
        self.worker.post("stop")

    def set_selection(self, selection: CaptureSelection, source_label: str) -> None:
        self.selection = selection
        self.source_label = source_label
        if self.session:
            self.session.source = source_label
        self.worker.post("set_selection", selection=selection)

    def set_language(self, language: str | None) -> None:
        self.worker.post("set_language", language=language)

    def set_opencc(self, enabled: bool) -> None:
        self.worker.post("set_opencc", enabled=enabled)

    def shutdown(self) -> None:
        self.worker.post("quit")
        self.worker.wait(10000)
        self._save_history()

    @property
    def is_running(self) -> bool:
        return self.run_state in (RUNNING, PAUSED)

    # -- slots ------------------------------------------------------------------
    def _on_final(self, text: str, start: float, end: float) -> None:
        if text and self.session is not None:
            self.session.lines.append(HistoryLine(start=round(start, 2), end=round(end, 2), text=text, wall=time.time()))
            self._dirty = True
        self.final.emit(text, start, end)

    def _on_model_state(self, state: str, model_id: str) -> None:
        self.model_state = state
        self.modelState.emit(state, model_id)

    def _on_device(self, device: str, compute_type: str) -> None:
        self.device = device
        self.compute_type = compute_type
        self.deviceChanged.emit(device, compute_type)

    def _on_run_state(self, state: str) -> None:
        if state == RUNNING and self.session is None:
            info = get_model(self.model_id or "")
            self.session = self.history.new_session(info.name if info else "", self.source_label)
        elif state == IDLE:
            self._save_history()
            self.session = None
        self.run_state = state
        self.runState.emit(state)

    def _save_history(self) -> None:
        if self._dirty and self.session is not None:
            self.history.save(self.session)
            self._dirty = False
