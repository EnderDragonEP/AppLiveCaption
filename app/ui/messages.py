"""User-facing texts for errors and notices, shown as Fluent InfoBars."""

from __future__ import annotations

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar, InfoBarPosition

from ..i18n import tr


def _device_kind(payload: dict) -> str:
    return tr("microphone") if payload.get("kind") == "mic" else tr("output device")


def error_text(kind: str, detail: str) -> tuple[str, str]:
    """(title, content) for a worker error."""
    if kind == "no_model":
        return tr("No model loaded"), tr("Pick a model and download it before starting.")
    if kind == "model_missing":
        return tr("Model not downloaded"), tr("{detail}. Download it on the Models page.").format(detail=detail)
    if kind == "model_corrupt":
        return tr("Model files are damaged"), tr(
            "The model could not be loaded ({detail}). Delete it on the Models page and download it again."
        ).format(detail=detail)
    if kind == "oom":
        return tr("Not enough memory"), tr("{detail} does not fit in memory. Pick a smaller model.").format(detail=detail)
    if kind == "backend_missing":
        return tr("Model needs PyTorch"), tr(
            "This model runs with PyTorch + transformers, which are not installed ({detail})."
        ).format(detail=detail)
    if kind == "language_unsupported":
        return tr("Language not supported"), tr(
            "The selected model does not support language '{code}'. Switched to auto-detect."
        ).format(code=detail)
    if kind == "capture_failed":
        return tr("Could not capture audio"), detail
    if kind == "transcribe_failed":
        return tr("Transcription error"), detail
    if kind == "unknown_model":
        return tr("Unknown model"), detail
    return tr("Something went wrong"), detail


def notice_text(kind: str, payload: dict) -> tuple[str, str, str] | None:
    """(level, title, content) for a notice; level is info/warning/success/error."""
    name = payload.get("name") or payload.get("app") or ""
    if kind == "device_lost":
        return "warning", tr("Audio device disconnected"), tr(
            "The {kind} stopped working. Reconnecting…").format(kind=_device_kind(payload))
    if kind == "device_missing":
        return "error", tr("No audio device"), tr(
            "No {kind} is available. Captioning resumes when one is connected.").format(kind=_device_kind(payload))
    if kind == "device_restored":
        return "success", tr("Audio device reconnected"), payload.get("device", "")
    if kind == "device_fallback":
        return "warning", tr("Using the default device"), tr(
            "The chosen {kind} is not connected, so the default one is used: {device}."
        ).format(kind=_device_kind(payload), device=payload.get("device", ""))
    if kind == "device_changed":
        return "info", tr("Audio device changed"), tr("Switched to the new default device.")
    if kind == "app_closed":
        return "warning", tr("{name} closed").format(name=name), tr(
            "Still captioning the other selected apps. {name} is picked up again when it reopens."
        ).format(name=name)
    if kind == "app_resumed":
        return "success", tr("{name} is back").format(name=name), tr("Captioning {name} again.").format(name=name)
    if kind == "app_error":
        return "error", tr("Can't capture {name}").format(name=name), payload.get("error", "")
    if kind == "per_app_unsupported":
        return "error", tr("Per-app capture not supported"), tr(
            "Captioning individual apps needs Windows 11 (build 20348 or later). Use System audio instead.")
    if kind == "slow_model":
        if payload.get("risk") == "cpu_slow":
            body = tr("{model} on the CPU will probably lag behind real time. "
                      "Try Whisper small or base, or use an NVIDIA GPU.")
        else:
            body = tr("{model} may not fit in this GPU's memory. Try Whisper large-v3-turbo or a smaller model.")
        return "warning", tr("This model may be too slow"), body.format(model=payload.get("model", ""))
    if kind == "gpu_oom_cpu_fallback":
        return "warning", tr("GPU out of memory"), tr(
            "{model} did not fit in GPU memory, so it runs on the CPU now. Pick a smaller model for real time."
        ).format(model=payload.get("model", ""))
    if kind == "gpu_failed_cpu_fallback":
        return "warning", tr("GPU error"), tr(
            "The GPU reported an error, so {model} runs on the CPU now. Restart the app to use the GPU again."
        ).format(model=payload.get("model", ""))
    if kind == "falling_behind":
        return "warning", tr("Captions are falling behind"), tr(
            "Recognition can't keep up, so some audio was skipped. Pick a smaller model.")
    return None


def show_infobar(level: str, title: str, content: str, parent, duration: int = 6000) -> InfoBar:
    factory = {"info": InfoBar.info, "warning": InfoBar.warning, "success": InfoBar.success, "error": InfoBar.error}
    return factory.get(level, InfoBar.info)(
        title=title, content=content, orient=Qt.Orientation.Horizontal if len(content) < 60 else Qt.Orientation.Vertical,
        isClosable=True, duration=duration, position=InfoBarPosition.TOP_RIGHT, parent=parent,
    )
