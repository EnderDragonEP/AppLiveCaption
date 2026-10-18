"""Main window: navigation, pages, overlay, tray, and wiring to the controller."""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, QTimer
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition, SplashScreen

from ..asr.hardware import gpu_info
from ..config import cfg
from ..core.controller import IDLE, MODEL_FAILED, MODEL_LOADING, MODEL_READY, PAUSED, RUNNING, CaptionController
from ..models.catalog import get_model
from ..models.storage import is_downloaded
from .async_utils import run_async
from .caption_model import CaptionModel
from .icons import app_icon
from .live_page import LivePage
from .messages import error_text, notice_text, show_infobar
from .source_picker import selection_summary

log = logging.getLogger(__name__)

APP_TITLE = "AppLiveCaption"


def warm_up_native_libraries() -> None:
    """Import CTranslate2/faster-whisper and onnxruntime (Silero VAD) up front."""
    from ..asr.cuda_setup import setup_cuda_dlls

    setup_cuda_dlls()
    try:
        import faster_whisper  # noqa: F401  (loads ctranslate2.dll and cuDNN)

        from ..audio.vad import get_session

        get_session()
    except Exception:  # noqa: BLE001 - reported later when a model loads
        log.exception("warm-up failed")


class MainWindow(FluentWindow):
    def __init__(self, auto_start: bool = False):
        super().__init__()
        self._auto_start = auto_start
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1080, 720)
        self.setMinimumSize(820, 520)
        self.setMicaEffectEnabled(cfg.get(cfg.micaEnabled))
        cfg.micaEnabled.valueChanged.connect(self.setMicaEffectEnabled)

        # Show a splash screen while native libraries load: their imports hold
        # the GIL, so doing it later would freeze the UI for a moment.
        self.splash = SplashScreen(self.windowIcon(), self)
        self.splash.setIconSize(QSize(96, 96))
        self.show()
        QApplication.processEvents()
        warm_up_native_libraries()

        self.captions = CaptionModel(parent=self)
        self.controller = CaptionController(self)
        self._gpu_name = ""
        self._pending_model: str | None = None  # model to switch to once its download finishes

        # Pages
        self.live = LivePage(self.captions, self)
        self.addSubInterface(self.live, FluentIcon.QUICK_NOTE, self.tr("Live captions"))
        self._create_pages()
        self._fit_navigation()

        # Live page -> controller
        self.live.startRequested.connect(self.start_captioning)
        self.live.stopRequested.connect(self.controller.stop)
        self.live.modelRequested.connect(self.request_model)
        self.live.languageChanged.connect(lambda c: self.controller.set_language(None if c == "auto" else c))
        self.live.selectionChanged.connect(self._on_selection_changed)
        self.live.overlayToggled.connect(self.set_overlay_visible)
        cfg.opencc.valueChanged.connect(self.controller.set_opencc)

        # Controller -> UI
        c = self.controller
        c.partial.connect(self.captions.set_partial)
        c.final.connect(lambda text, _s, _e: self.captions.add_final(text))
        c.latency.connect(self.live.set_latency)
        c.modelState.connect(self._on_model_state)
        c.deviceChanged.connect(self._on_device_changed)
        c.runState.connect(self._on_run_state)
        c.error.connect(self._on_error)
        c.notice.connect(self._on_notice)
        c.languagesChanged.connect(self._on_languages)
        c.levels.connect(self.live.set_levels)

        self._setup_overlay_and_tray()
        run_async(gpu_info, self._on_gpu_info, self)
        self.splash.finish()
        QTimer.singleShot(0, self._load_initial_model)

    # -- pages that depend on later milestones ----------------------------------
    NAV_EXPANDED_WIDTH = 200  # wide enough for "Live captions" with its icon

    def _fit_navigation(self) -> None:
        """Size the navigation panel and the window minimum to the content.

        A hard-coded minimum let the Live page's toolbar get narrower than its
        widgets, so the loading spinner was drawn on top of the model dropdown.
        """
        nav = self.navigationInterface
        nav.setExpandWidth(self.NAV_EXPANDED_WIDTH)
        pages = [self.stackedWidget.widget(i) for i in range(self.stackedWidget.count())]
        content = max(p.minimumSizeHint().width() for p in pages)
        collapsed = nav.width() or 48
        min_width = max(820, content + collapsed + 8)
        self.setMinimumWidth(min_width)
        # The expanded panel sits beside the pages only when they still fit next
        # to it; in narrower windows it opens over them instead.
        nav.setMinimumExpandWidth(max(1008, min_width + self.NAV_EXPANDED_WIDTH - collapsed))

    def _create_pages(self) -> None:
        from .history_page import HistoryPage
        from .models_page import ModelsPage
        from .settings_page import SettingsPage

        self.models_page = ModelsPage(self)
        self.addSubInterface(self.models_page, FluentIcon.LIBRARY, self.tr("Models"))
        self.history_page = HistoryPage(self.controller.history, self)
        self.addSubInterface(self.history_page, FluentIcon.HISTORY, self.tr("History"))
        self.settings_page = SettingsPage(self)
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, self.tr("Settings"), NavigationItemPosition.BOTTOM)

        self.models_page.useRequested.connect(self.request_model)
        self.models_page.downloadRequested.connect(self.download_model)
        self.models_page.modelsChanged.connect(self.live.refresh_models)
        self.models_page.downloadFinished.connect(self.on_download_finished)
        self.settings_page.deviceChanged.connect(self._on_device_setting)
        self.settings_page.modelFolderChanged.connect(self._on_model_folder)
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

    def _on_device_setting(self, device: str) -> None:
        # Reload the current model on the newly chosen device.
        if self.controller.model_id and self.controller.model_state == MODEL_READY:
            self.controller.load_model(self.controller.model_id, device)

    def _on_model_folder(self, _folder: str) -> None:
        self.models_page.refresh()
        self.live.refresh_models()

    def _on_page_changed(self, _index: int) -> None:
        widget = self.stackedWidget.currentWidget()
        if widget is self.history_page:
            self.history_page.reload()
        elif widget is self.models_page:
            self.models_page.refresh()

    # -- models ---------------------------------------------------------------
    def _load_initial_model(self) -> None:
        """Load the default model from Settings, or else the last one used, if downloaded."""
        candidates = [cfg.get(cfg.defaultModel), cfg.get(cfg.modelId)]
        chosen = next((m for m in candidates if get_model(m) and is_downloaded(get_model(m))), None)
        if chosen:
            cfg.set(cfg.modelId, chosen)
            self.live.select_model(chosen)
            self.controller.load_model(chosen, cfg.get(cfg.device))
        else:
            if get_model(candidates[0]):
                cfg.set(cfg.modelId, candidates[0])
                self.live.select_model(candidates[0])
            self.live.set_device(self.tr("No model loaded"))

    def request_model(self, model_id: str) -> None:
        """The user picked a model: load it, or offer to download it first."""
        info = get_model(model_id)
        if info is None:
            return
        c = self.controller
        if model_id == c.model_id and c.model_state in (MODEL_READY, MODEL_LOADING):
            return  # already in use
        if is_downloaded(info):
            cfg.set(cfg.modelId, model_id)
            self.live.select_model(model_id)
            c.load_model(model_id, cfg.get(cfg.device))
            return
        # Keep the dropdown on the model that is actually in use.
        self.live.select_model(self.controller.model_id or cfg.get(cfg.modelId))
        self.download_model(model_id, ask=True)

    def download_model(self, model_id: str, ask: bool = False) -> None:
        from .download import confirm_download

        info = get_model(model_id)
        if info is None:
            return
        if ask and not confirm_download(info, self):
            return
        self._pending_model = model_id
        self.models_page.start_download(info)

    def on_download_finished(self, model_id: str, ok: bool) -> None:
        self.live.refresh_models()
        if ok and self._pending_model == model_id:
            self._pending_model = None
            self.request_model(model_id)

    def _on_model_state(self, state: str, model_id: str) -> None:
        info = get_model(model_id)
        self.live.set_model_loading(state == MODEL_LOADING, info)
        if state == MODEL_READY:
            self.live.select_model(model_id)
            self.models_page.set_current_model(model_id)
            self._update_device_label()
            if self._auto_start and not self.controller.is_running:
                self._auto_start = False
                self.start_captioning()
        elif state == MODEL_FAILED:
            self.models_page.set_current_model(None)
            self.live.set_device(self.tr("No model loaded"))

    def _on_device_changed(self, _device: str, _compute: str) -> None:
        self._update_device_label()

    def _on_gpu_info(self, gpu) -> None:
        self._gpu_name = gpu.name if gpu else ""
        self._update_device_label()

    def _update_device_label(self) -> None:
        c = self.controller
        if c.model_state != MODEL_READY:
            return
        info = get_model(c.model_id or "")
        if c.device == "cuda":
            dev = self.tr("GPU") + (f" · {self._gpu_name}" if self._gpu_name else "")
        else:
            dev = self.tr("CPU")
        self.live.set_device(f"{info.name if info else ''}  ·  {dev}  ·  {c.compute_type}")

    def _on_languages(self, codes: list) -> None:
        self.live.set_languages(codes, get_model(self.controller.model_id or ""))

    # -- captioning -------------------------------------------------------------
    def start_captioning(self) -> None:
        c = self.controller
        if c.model_state not in (MODEL_READY, MODEL_LOADING):
            info = get_model(cfg.get(cfg.modelId))
            if info and not is_downloaded(info):
                # First run: offer to download the selected model instead of failing.
                self.download_model(info.id, ask=True)
                return
        sel = self.live.selection
        lang = self.live.selected_language()
        self.captions.clear()
        self.controller.start(sel, None if lang == "auto" else lang, cfg.get(cfg.opencc), selection_summary(sel))

    def _on_selection_changed(self, sel) -> None:
        if self.controller.is_running:
            self.controller.set_selection(sel, selection_summary(sel))

    def _on_run_state(self, state: str) -> None:
        self.live.set_running(state in (RUNNING, PAUSED), paused=state == PAUSED)
        if hasattr(self, "tray"):
            self.tray.set_running(state != IDLE)

    def toggle_captioning(self) -> None:
        if self.controller.is_running:
            self.controller.stop()
        else:
            self.start_captioning()

    # -- errors and notices -------------------------------------------------------
    def _on_error(self, kind: str, detail: str) -> None:
        log.warning("error %s: %s", kind, detail)
        if kind == "language_unsupported":
            self.live.reset_language()
        title, content = error_text(kind, detail)
        show_infobar("error", title, content, self, duration=10000)

    def _on_notice(self, kind: str, payload: dict) -> None:
        msg = notice_text(kind, payload)
        if msg:
            level, title, content = msg
            show_infobar(level, title, content, self, duration=8000 if level != "error" else 12000)

    # -- overlay and tray --------------------------------------------------------------
    def _setup_overlay_and_tray(self) -> None:
        from .overlay import CaptionOverlay
        from .tray import TrayIcon

        self.overlay = CaptionOverlay(self.captions)
        self.overlay.closed.connect(lambda: self.set_overlay_visible(False))
        self.tray = TrayIcon(self)
        self.tray.showWindowRequested.connect(self.show_and_raise)
        self.tray.overlayToggled.connect(self.set_overlay_visible)
        self.tray.captionToggled.connect(self.toggle_captioning)
        self.tray.clickThroughToggled.connect(lambda on: cfg.set(cfg.overlayClickThrough, on))
        self.tray.quitRequested.connect(self.close)
        self.tray.show()
        if cfg.get(cfg.overlayVisible):
            QTimer.singleShot(300, lambda: self.set_overlay_visible(True))

    def set_overlay_visible(self, on: bool) -> None:
        cfg.set(cfg.overlayVisible, on)
        self.overlay.setVisible(on)
        self.live.set_overlay_checked(on)
        self.tray.set_overlay_checked(on)

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):  # noqa: N802
        self.overlay.save_geometry()
        self.overlay.close()
        self.tray.hide()
        self.models_page.shutdown()
        self.controller.shutdown()
        super().closeEvent(e)
        QApplication.quit()
