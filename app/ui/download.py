"""Download prompt and background download management."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QThread, Signal

from ..asr import cuda_libs
from ..i18n import tr
from ..models.catalog import ModelInfo
from ..models.downloader import DownloadCancelled, DownloadError, Progress, TransferSpeed, download_model

log = logging.getLogger(__name__)


def format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit in ("MB", "GB") else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def download_error_text(kind: str, message: str) -> str:
    if kind == "disk_space":
        return tr("Not enough free disk space ({detail}). Free some space or change the model folder in Settings.").format(detail=message)
    if kind == "not_found":
        return tr("The model was not found ({detail}).").format(detail=message)
    if kind == "io":
        return tr("The model files could not be written ({detail}).").format(detail=message)
    return tr("Check your internet connection. The download resumes where it stopped next time. ({detail})").format(detail=message)


def cuda_download_error_text(kind: str, message: str) -> str:
    if kind == "disk_space":
        return tr("Not enough free disk space ({detail}).").format(detail=message)
    if kind == "io":
        return tr("The files could not be written ({detail}).").format(detail=message)
    if kind == "corrupt":
        return tr("A downloaded file was damaged and has been deleted. Try again. ({detail})").format(detail=message)
    return tr("Check your internet connection. The download resumes where it stopped next time. ({detail})").format(detail=message)


class CudaDownloadThread(QThread):
    """Downloads NVIDIA's CUDA libraries (``cuda_libs.download``)."""

    progress = Signal(object)  # Progress
    done = Signal(str, str)  # result ("ok"/"cancelled"/error kind), message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_event = threading.Event()

    def run(self) -> None:
        from ..asr.hardware import wants_legacy_engine

        speed = TransferSpeed()
        try:
            cuda_libs.download(lambda done, total, name: self.progress.emit(Progress(done, total, speed.add(done), name)),
                               self.cancel_event, legacy=wants_legacy_engine())
        except cuda_libs.DownloadCancelled:
            self.done.emit("cancelled", "")
        except cuda_libs.DownloadError as exc:
            log.warning("CUDA library download failed: %s", exc)
            self.done.emit(exc.kind, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("CUDA library download failed")
            self.done.emit("network", str(exc))
        else:
            self.done.emit("ok", "")


class _DownloadThread(QThread):
    progress = Signal(str, object)
    done = Signal(str, str, str)  # model id, result ("ok"/"cancelled"/error kind), message

    def __init__(self, info: ModelInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            download_model(self.info, lambda p: self.progress.emit(self.info.id, p), self.cancel_event)
        except DownloadCancelled:
            self.done.emit(self.info.id, "cancelled", "")
        except DownloadError as exc:
            log.warning("download of %s failed: %s", self.info.id, exc)
            self.done.emit(self.info.id, exc.kind, str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("download of %s failed", self.info.id)
            self.done.emit(self.info.id, "network", str(exc))
        else:
            self.done.emit(self.info.id, "ok", "")


class DownloadManager(QObject):
    progress = Signal(str, object)  # model id, Progress
    finished = Signal(str, str, str)  # model id, result, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: dict[str, _DownloadThread] = {}

    def start(self, info: ModelInfo) -> None:
        if info.id in self._threads:
            return
        t = _DownloadThread(info, self)
        t.progress.connect(self.progress)
        t.done.connect(self._on_done)
        self._threads[info.id] = t
        t.start()

    def cancel(self, model_id: str) -> None:
        t = self._threads.get(model_id)
        if t:
            t.cancel_event.set()

    def is_active(self, model_id: str) -> bool:
        return model_id in self._threads

    def any_active(self) -> bool:
        return bool(self._threads)

    def cancel_all(self) -> None:
        for t in list(self._threads.values()):
            t.cancel_event.set()
        for t in list(self._threads.values()):
            t.wait(5000)

    def _on_done(self, model_id: str, result: str, message: str) -> None:
        t = self._threads.pop(model_id, None)
        if t:
            t.wait(1000)
            t.deleteLater()
        self.finished.emit(model_id, result, message)


__all__ = ["CudaDownloadThread", "DownloadManager", "Progress", "cuda_download_error_text", "download_error_text",
           "format_bytes"]
