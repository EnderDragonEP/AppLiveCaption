"""Main window: navigation, pages, overlay, tray, and wiring to the controller."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QEvent, QProcess, QSize, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, MessageBox, NavigationItemPosition, PushButton, SplashScreen

from .. import APP_NAME, __version__
from ..asr import cuda_libs
from ..asr.hardware import cuda_available, nvidia_gpu, refresh_cuda
from ..config import NO_MODEL, PREVIOUS_MODEL, cfg
from ..core.controller import IDLE, MODEL_FAILED, MODEL_LOADING, MODEL_NONE, MODEL_READY, PAUSED, RUNNING, CaptionController
from ..models.catalog import get_model, load_catalog
from ..models.storage import disk_usage, is_downloaded
from ..updates import UpdateChecker, is_newer
from .async_utils import run_async
from .caption_model import CaptionModel
from .model_dialogs import ConfirmDialog
from .download import format_bytes
from .icons import app_icon
from .live_page import LivePage
from .messages import error_text, notice_text, show_infobar
from .source_picker import selection_summary

log = logging.getLogger(__name__)

APP_TITLE = APP_NAME


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


def probe_gpu(refresh: bool = False) -> tuple[str | None, bool]:
    """(NVIDIA GPU name or None, whether models can run on it). ``refresh``
    looks for the CUDA libraries again, after they were downloaded."""
    usable = refresh_cuda() if refresh else cuda_available()
    gpu = nvidia_gpu()
    return (gpu.name if gpu else None), usable


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
        self._after_unload = None  # runs once the controller has released the model (see _reset_catalog)
        self._load_after_gpu: str | None = None  # a downloaded model waiting for GPU support's download
        self._launched_with_start = auto_start
        self._setup_checked = False  # first-time setup was considered (it's offered at the first GPU probe)
        self._setup_open = False

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
        cfg.chineseOutput.valueChanged.connect(self.controller.set_chinese_output)

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
        run_async(probe_gpu, self._on_gpu_probe, self)
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
        from .keyword_service import KeywordService
        from .keywords_page import KeywordsPage
        from .models_page import ModelsPage
        from .settings_page import SettingsPage

        # Navigation order: Live captions, Keywords, Models, History (Settings at the bottom).
        self.keywords = KeywordService(self.captions, self)
        self.keywords_page = KeywordsPage(self.keywords, self)
        self.keywords_page.testNotificationRequested.connect(self._test_keyword_notification)
        self.addSubInterface(self.keywords_page, FluentIcon.TAG, self.tr("Keywords"))
        self.models_page = ModelsPage(self)
        self.addSubInterface(self.models_page, FluentIcon.LIBRARY, self.tr("Models"))
        self.history_page = HistoryPage(self.controller.history, self.keywords, self)
        self.addSubInterface(self.history_page, FluentIcon.HISTORY, self.tr("History"))
        self.keywords.changed.connect(self.history_page.mark_keywords)
        self.keywords.heard.connect(self._on_keyword_heard)
        self.settings_page = SettingsPage(self)
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, self.tr("Settings"), NavigationItemPosition.BOTTOM)

        self.models_page.useRequested.connect(self.request_model)
        self.models_page.unloadRequested.connect(self.unload_model)
        self.models_page.downloadRequested.connect(self.download_model)
        self.models_page.modelsChanged.connect(self.live.refresh_models)
        self.models_page.downloadFinished.connect(self.on_download_finished)
        self.models_page.gpuSupportInstalled.connect(self._on_gpu_support_installed)
        self.models_page.gpu_card.downloadEnded.connect(self._on_gpu_download_ended)
        self.models_page.catalogChanged.connect(self._on_catalog_changed)
        self.settings_page.resetCatalogRequested.connect(self._reset_catalog)
        self.settings_page.setupRequested.connect(self.show_first_run_setup)
        self.settings_page.languageChanged.connect(self._offer_restart)
        self.history_page.refreshRequested.connect(self._reload_history)
        self.settings_page.deviceChanged.connect(self._on_device_setting)
        self.settings_page.modelFolderChanged.connect(self._on_model_folder)
        self.settings_page.about_card.clicked.connect(self.check_for_updates)
        self.stackedWidget.currentChanged.connect(self._on_page_changed)

    def _on_device_setting(self, device: str) -> None:
        # Reload the current model on the newly chosen device.
        if self.controller.model_id and self.controller.model_state == MODEL_READY:
            self.controller.load_model(self.controller.model_id, device)

    # -- the model list ---------------------------------------------------------------
    def _on_catalog_changed(self) -> None:
        """Models were added, removed or restored: bring the other pages in line."""
        self._forget_removed_default()
        self.settings_page.refresh_models()
        c = self.controller
        if get_model(c.model_id or "") is None and c.model_state in (MODEL_READY, MODEL_LOADING):
            c.unload_model()  # the model in use is no longer in the list
        self.live.refresh_models()  # a selected model that left the list: "Select a model"

    def _forget_removed_default(self) -> bool:
        """Settings → Default model is a model that's no longer in the list: make it None, instead of a
        blank choice that quietly loads the model used last. Returns whether it changed."""
        default = cfg.get(cfg.defaultModel)
        if default in (NO_MODEL, PREVIOUS_MODEL) or get_model(default) is not None:
            return False
        cfg.set(cfg.defaultModel, NO_MODEL)
        return True

    def _reset_catalog(self) -> None:
        """Settings → Reset model list: the presets come back, the models the user added are removed."""
        if self._after_unload is not None:
            return  # a reset is already waiting for the model in use to be released
        if self.models_page.downloads.any_active():
            show_infobar("warning", self.tr("A download is running"),
                         self.tr("Wait for it to finish (or cancel it) before resetting the model list."), self)
            return
        added = [m for m in load_catalog() if m.custom]
        files = sum(disk_usage(m) for m in added)
        content = self.tr("The preset models come back in the list.")
        if added:
            content += " " + self.tr("The {n} model(s) you added are removed from it.").format(n=len(added))
        dialog = ConfirmDialog(self.tr("Reset the model list?"), content, self.tr("Reset"),
                               option=(self.tr("Also delete the files of the models you added ({size})")
                                       .format(size=format_bytes(files)) if files else ""), parent=self)
        if not dialog.exec():
            return
        delete_files = dialog.option_checked
        in_use = self.controller.model_id
        if in_use and any(m.id == in_use for m in added) and self.controller.model_state in (MODEL_READY, MODEL_LOADING):
            # Release the model first: Windows won't delete a file that is loaded.
            self._after_unload = lambda: self.models_page.reset_catalog(delete_files)
            self.controller.unload_model()
        else:
            self.models_page.reset_catalog(delete_files)

    def _on_model_folder(self, _folder: str) -> None:
        self.models_page.refresh()
        self.live.refresh_models()

    def _on_page_changed(self, _index: int) -> None:
        widget = self.stackedWidget.currentWidget()
        if widget is self.history_page:
            self._reload_history()
        elif widget is self.models_page:
            self.models_page.refresh()

    def _reload_history(self) -> None:
        self.controller.save_history()  # include lines captioned since the last periodic save
        self.history_page.reload()

    # -- models ---------------------------------------------------------------
    def _load_initial_model(self) -> None:
        """Load Settings → Default model: none, the one used last time, or a model (or else the
        last one used) if it's downloaded."""
        if self._forget_removed_default():
            self.settings_page.refresh_models()
        default = cfg.get(cfg.defaultModel)
        if default == NO_MODEL and self._auto_start:
            default = PREVIOUS_MODEL  # --start: caption with the model used last time
        if default == NO_MODEL:
            candidates = []
        elif default == PREVIOUS_MODEL:
            candidates = [cfg.get(cfg.modelId)]
        else:
            candidates = [default, cfg.get(cfg.modelId)]
        chosen = next((m for m in candidates if get_model(m) and is_downloaded(get_model(m))), None)
        if chosen:
            cfg.set(cfg.modelId, chosen)
            self.live.select_model(chosen)
            self.controller.load_model(chosen, cfg.get(cfg.device))
            return
        # Nothing to load: show the chosen model if it isn't downloaded (Start then offers to download
        # it), else "Select a model". --start is dropped too: it would otherwise start captioning as soon
        # as the user loads a model later.
        self._auto_start = False
        self.live.select_model(next((m for m in candidates if get_model(m)), None))
        self.live.set_device(self.tr("No model loaded"))

    # -- first-time setup ---------------------------------------------------------
    def _gpu_to_offer(self) -> str | None:
        """The NVIDIA GPU that downloading GPU support would let models run on, if that's worth offering."""
        gpu = self.models_page.gpu_card
        if (gpu.gpu_name and not gpu.usable and cfg.get(cfg.device) != "cpu" and not gpu.downloading
                and cuda_libs.installed_dir() is None and not cuda_libs.removal_scheduled()):
            return gpu.gpu_name
        return None

    def show_first_run_setup(self) -> None:
        """Ask for GPU support, a model and the model at start-up (first start, or Settings → First-time setup)."""
        from .setup_dialog import FirstRunDialog

        if self._setup_open:
            return
        self._setup_open = True
        try:
            dialog = FirstRunDialog(self._gpu_to_offer(), bool(self._gpu_name), nvidia_gpu(),
                                    cfg.get(cfg.defaultModel), self)
            accepted = dialog.exec()
            cfg.set(cfg.setupDone, True)  # skipped counts too: it isn't shown at start-up again
            if accepted:
                self._apply_setup(dialog.choices())
        finally:
            self._setup_open = False

    def _apply_setup(self, choices) -> None:
        cfg.set(cfg.defaultModel, choices.default_model)
        self.settings_page.refresh_models()
        downloads = []
        if choices.gpu_support and self._gpu_to_offer():
            self.models_page.gpu_card.start_download()
            downloads.append(self.tr("GPU support"))
        info = get_model(choices.model_id or "")
        if info is not None:
            if is_downloaded(info):
                self.request_model(info.id)
            else:
                cfg.set(cfg.modelId, info.id)  # "the model I used last" from now on
                self.live.select_model(info.id)
                self.download_model(info.id)
                downloads.append(info.name)
        if downloads:
            self.switchTo(self.models_page)
            show_infobar("info", self.tr("Downloading {what}").format(what=self.tr(" and ").join(downloads)),
                         self.tr("Follow the progress here. The model loads when it's ready.") if info and
                         not is_downloaded(info) else self.tr("Follow the progress here."), self)

    def request_model(self, model_id: str) -> None:
        """The user picked a model: load it, or show its card on the Models page to download it."""
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
        # Keep the dropdown on the model that is actually in use, and show the
        # model's card so the user can read about it and download it from there.
        self.live.select_model(c.model_id if c.model_state in (MODEL_READY, MODEL_LOADING) else None)
        self.offer_download(info, self.tr("Download it here, and the app switches to it when it's done."))

    def unload_model(self, model_id: str) -> None:
        """The in-use card's Unload: release the model; the dropdown shows "Select a model" until one is picked."""
        c = self.controller
        if model_id == c.model_id and c.model_state == MODEL_READY:
            c.unload_model()

    def show_model_card(self, model_id: str) -> None:
        self.switchTo(self.models_page)
        self.models_page.show_model(model_id)

    def offer_download(self, info, what_next: str) -> None:
        """The model isn't downloaded: show its card, highlighted, and say so."""
        self.show_model_card(info.id)
        show_infobar("info", self.tr("{name} isn't downloaded").format(name=info.name), what_next, self)

    def download_model(self, model_id: str) -> None:
        info = get_model(model_id)
        if info is None:
            return
        self._pending_model = model_id
        self.models_page.start_download(info)

    def on_download_finished(self, model_id: str, ok: bool) -> None:
        self.live.refresh_models()
        if ok and self._pending_model == model_id:
            self._pending_model = None
            if self.models_page.gpu_card.downloading:
                # GPU support is on its way (first-time setup starts both): load the model once it's there,
                # rather than on the CPU now, with a "too slow" warning, and again on the GPU later.
                self._load_after_gpu = model_id
                return
            self.request_model(model_id)

    def _on_gpu_download_ended(self, ok: bool) -> None:
        if ok or self._load_after_gpu is None:
            return  # (installed: _on_gpu_support_probed loads the waiting model, on the GPU)
        model_id, self._load_after_gpu = self._load_after_gpu, None
        self.request_model(model_id)  # GPU support failed or was cancelled: use the CPU

    def _on_model_state(self, state: str, model_id: str) -> None:
        info = get_model(model_id)
        self.live.set_model_loading(state == MODEL_LOADING, info)
        if state == MODEL_LOADING:
            # Its files are open: keep them from being deleted (or the model removed) until it's loaded.
            self.models_page.set_current_model(model_id, loading=True)
        elif state == MODEL_READY:
            self.live.select_model(model_id)
            self.models_page.set_current_model(model_id)
            self._update_device_label()
            if self._auto_start and not self.controller.is_running:
                self._auto_start = False
                self.start_captioning()
        elif state in (MODEL_FAILED, MODEL_NONE):
            self.models_page.set_current_model(None)
            self.live.set_device(self.tr("No model loaded"))
            self._auto_start = False  # (it waited for this model)
            if state == MODEL_NONE:
                self.live.select_model(None)  # "Select a model"
            if state == MODEL_NONE and self._after_unload:
                after, self._after_unload = self._after_unload, None
                after()

    def _on_device_changed(self, _device: str, _compute: str) -> None:
        self._update_device_label()

    def _on_gpu_probe(self, result) -> None:
        name, usable = result
        self._gpu_name = name if usable else ""
        self._update_device_label()
        self.models_page.set_gpu(name, usable)
        if not self._setup_checked:
            # First start: the setup needs to know about the GPU, so it opens once the GPU was probed. (Not for
            # a start minimized at sign-in or with --start: it waits for the next normal start.)
            self._setup_checked = True
            if not cfg.get(cfg.setupDone) and not self._launched_with_start and not self.isMinimized():
                QTimer.singleShot(300, self.show_first_run_setup)

    def _on_gpu_support_installed(self) -> None:
        run_async(lambda: probe_gpu(refresh=True), self._on_gpu_support_probed, self)

    def _on_gpu_support_probed(self, result) -> None:
        name, usable = result
        self._on_gpu_probe(result)
        if self._load_after_gpu is not None:
            model_id, self._load_after_gpu = self._load_after_gpu, None
            self.request_model(model_id)  # downloaded while GPU support was; on the GPU now if it can be used
        if not usable:
            show_infobar("warning", self.tr("GPU support installed, but the GPU can't be used"), self.tr(
                "Update the NVIDIA graphics driver, then restart the app. Models keep running on the CPU."),
                self, duration=12000)
            return
        show_infobar("success", self.tr("GPU support installed"),
                     self.tr("Models now run on your {gpu}.").format(gpu=name), self)
        c = self.controller
        if c.model_id and c.model_state == MODEL_READY and c.device == "cpu" and cfg.get(cfg.device) != "cpu":
            c.load_model(c.model_id, cfg.get(cfg.device))  # move the loaded model to the GPU

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
            info = get_model(self.live.selected_model_id() or "")
            if info is None and load_catalog():  # no model selected
                show_infobar("info", self.tr("Select a model first"),
                             self.tr("Pick one in the Model dropdown, then press Start."), self)
                return
            if info is None:  # the list is empty
                self.switchTo(self.models_page)
                return
            if not is_downloaded(info):
                # First run: show where to download the selected model instead of failing.
                self.offer_download(info, self.tr("Download it here, then press Start."))
                return
            # Selected but not loaded (its loading failed before): load it, then start.
            self._auto_start = True
            self.request_model(info.id)
            return
        sel = self.live.selection
        lang = self.live.selected_language()
        self.captions.clear()
        self.controller.start(sel, None if lang == "auto" else lang, cfg.get(cfg.chineseOutput), selection_summary(sel))

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
        self.tray.updateCheckRequested.connect(self._check_updates_from_tray)
        self.tray.quitRequested.connect(self.close)
        self.tray.messageClicked.connect(self.show_and_raise)  # a keyword notification
        self.tray.show()
        if cfg.get(cfg.overlayVisible):
            QTimer.singleShot(300, lambda: self.set_overlay_visible(True))

    def set_overlay_visible(self, on: bool) -> None:
        cfg.set(cfg.overlayVisible, on)
        self.overlay.setVisible(on)
        self.live.set_overlay_checked(on)
        self.tray.set_overlay_checked(on)

    # -- updates -------------------------------------------------------------------
    def check_for_updates(self) -> None:
        button = self.settings_page.about_card.button
        if not button.isEnabled():
            return  # a check is already running
        if not hasattr(self, "updates"):
            self.updates = UpdateChecker(self)
            self.updates.finished.connect(self._on_update_checked)
            self.updates.failed.connect(self._on_update_failed)
        self._set_checking(True)
        self.updates.check()

    def _check_updates_from_tray(self) -> None:
        self.show_and_raise()  # the result is shown in the window
        self.check_for_updates()

    def _set_checking(self, checking: bool) -> None:
        button = self.settings_page.about_card.button
        button.setEnabled(not checking)
        button.setText(self.tr("Checking…") if checking else self.tr("Check for updates"))

    def _on_update_checked(self, release) -> None:
        self._set_checking(False)
        if release is None or not is_newer(release.version):  # None: nothing published yet
            show_infobar("success", self.tr("You're up to date"),
                         self.tr("Version {version} is the latest version.").format(version=__version__), self, 3000)
            return
        box = MessageBox(self.tr("Version {version} is available").format(version=release.version),
                         self.tr("You have version {version}. Open the download page on GitHub?").format(version=__version__),
                         self)
        box.yesButton.setText(self.tr("Download"))
        box.cancelButton.setText(self.tr("Later"))
        if box.exec():
            QDesktopServices.openUrl(QUrl(release.url))

    def _on_update_failed(self, message: str) -> None:
        self._set_checking(False)
        show_infobar("warning", self.tr("Couldn't check for updates"), message, self, 5000)

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # -- keywords ---------------------------------------------------------------
    def _on_keyword_heard(self, keywords: str, line: str) -> None:
        """Keywords page: a Windows notification while the app is in the background (Windows plays its
        own sound with it), else the alert sound."""
        background = not self.isVisible() or self.isMinimized() or not self.isActiveWindow()
        if cfg.get(cfg.keywordNotify) and background and self.tray.isVisible():
            self.tray.showMessage(self.tr("Heard: {keywords}").format(keywords=keywords), line, app_icon(), 6000)
        elif cfg.get(cfg.keywordSound):
            self.keywords.play_sound()

    def _test_keyword_notification(self) -> None:
        """The Keywords page's Show button: a sample alert, with the first keyword in the list."""
        keyword = next((k["text"] for k in cfg.get(cfg.keywords) if k.get("enabled", True)), self.tr("keyword"))
        self.tray.showMessage(self.tr("Heard: {keywords}").format(keywords=keyword),
                              self.tr("This is how a keyword alert looks."), app_icon(), 6000)

    # -- language ---------------------------------------------------------------
    def _offer_restart(self) -> None:
        """Settings → Language changed: it applies when the app starts, so offer to restart now."""
        if getattr(self, "_restart_bar", None) is not None:
            self._restart_bar.close()  # (one bar, however often the language is changed)
        bar = self._restart_bar = show_infobar(
            "info", self.tr("Restart to change the language"),
            self.tr("The new language is used the next time the app starts."), self, duration=-1, with_buttons=True)
        button = PushButton(self.tr("Restart now"), bar)
        button.clicked.connect(self.restart)
        bar.addWidget(button)
        bar.closedSignal.connect(lambda: setattr(self, "_restart_bar", None))

    def restart(self) -> None:
        """Start a new copy of the app (as it was started, without --start or --minimized) and quit."""
        args = [a for a in sys.argv[1:] if a not in ("--start", "--minimized")]
        if not getattr(sys, "frozen", False):
            args = [sys.argv[0], *args]  # from source: the script (app/main.py or AppLiveCaption.pyw)
        if not QProcess.startDetached(sys.executable, args):
            show_infobar("error", self.tr("Couldn't restart"), self.tr("Close the app and start it again."), self)
            return
        self.close()

    def changeEvent(self, e):  # noqa: N802 - Qt override
        super().changeEvent(e)
        # Settings → Minimize to tray: a minimized window leaves the taskbar; the tray icon brings it back.
        if (e.type() == QEvent.Type.WindowStateChange and self.isMinimized() and cfg.get(cfg.minimizeToTray)
                and hasattr(self, "tray") and self.tray.isVisible()):
            QTimer.singleShot(0, self._hide_to_tray)  # (after Windows has finished minimizing it)

    def _hide_to_tray(self) -> None:
        if not self.isMinimized():
            return  # restored in the meantime
        self.hide()
        if not cfg.get(cfg.trayHintShown):
            cfg.set(cfg.trayHintShown, True)
            self.tray.showMessage(APP_NAME, self.tr("Still running here. Click to open."),
                                  app_icon(), 6000)

    def closeEvent(self, e):  # noqa: N802
        self.overlay.save_geometry()
        self.overlay.close()
        self.tray.hide()
        self.models_page.shutdown()
        self.controller.shutdown()
        super().closeEvent(e)
        QApplication.quit()
