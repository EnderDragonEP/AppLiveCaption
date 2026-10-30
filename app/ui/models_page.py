"""Models page: GPU support and the model list, with download, delete, add and remove, disk usage and license."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    RoundMenu,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
    ToolTipFilter,
    TransparentToolButton,
)

from .. import LICENSES_URL
from ..asr import cuda_libs
from ..asr.cuda_setup import active_dirs, cuda_libraries_found, legacy_engine_loaded
from ..asr.hardware import GpuInfo, nvidia_gpu, too_old_for_gpu_support, wants_legacy_engine
from ..i18n import language_label, tr
from ..models.catalog import ModelInfo, add_custom_model, load_catalog, remove_model, reset_catalog
from ..models.storage import (
    delete_model,
    disk_usage,
    has_partial_download,
    is_downloaded,
    model_path,
    models_dir,
)
from .download import CudaDownloadThread, DownloadManager, cuda_download_error_text, download_error_text, format_bytes
from .highlight import FlashMixin
from .messages import show_infobar
from .model_dialogs import AddModelDialog, ConfirmDialog


def languages_text(info: ModelInfo) -> str:
    if len(info.languages) > 10:
        return tr("{n} languages").format(n=len(info.languages))
    return tr(", ").join(language_label(c, info) for c in info.languages)  # (a translation may use its own separator)


class _HideOnEnter(QObject):
    """Hides the card's link hint while the mouse is on one of its buttons (they have their own tooltips,
    or would wrongly seem to open the link)."""

    def __init__(self, card_tip: ToolTipFilter, parent: QWidget):
        super().__init__(parent)
        self.card_tip = card_tip

    def eventFilter(self, obj, e):  # noqa: N802 - Qt override
        if e.type() == QEvent.Type.Enter:
            self.card_tip.hideToolTip()
        return False


class _ItemCard(FlashMixin, CardWidget):
    """A card that opens ``page_url`` on Ctrl+click and can be highlighted for a moment (``flash``).

    Subclasses call ``_build_frame`` (after setting ``page_url``), add ``detail`` (a label) for
    ``set_progress`` and, once their buttons exist, call ``_set_up_link_hint``.
    """

    page_url = ""
    link_hint = ""  # what Ctrl+click opens, shown when hovering the card
    PLAIN_CLICKS_FOR_TIP = 3  # plain clicks in a row (the user tries to open the link) before saying how
    _plain_clicks = 0
    _last_click = 0.0

    def _build_frame(self) -> QHBoxLayout:
        """Lay out the card: a row for the icon, text and status columns (returned
        for the subclass to fill), and under it the download progress bar across
        the whole card, hidden until a download runs."""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 16, 14)
        outer.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(16)
        outer.addLayout(row)
        self.progress = ProgressBar(self)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        outer.addWidget(self.progress)
        return row

    def _set_up_link_hint(self) -> None:
        """Show ``link_hint`` when hovering the card, except over its buttons."""
        self.setToolTip(self.link_hint)
        tip = ToolTipFilter(self, showDelay=500)
        self.installEventFilter(tip)
        for button in self.findChildren(QAbstractButton):
            button.installEventFilter(_HideOnEnter(tip, button))

    def mouseReleaseEvent(self, e):  # noqa: N802 - Qt override
        super().mouseReleaseEvent(e)
        # Buttons on the card handle their own clicks; this is a click on the card itself. Like a link in
        # Office it needs Ctrl, so a stray click doesn't open the browser. Ignore right-clicks and drags
        # released outside the card.
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._plain_clicks = 0
            QDesktopServices.openUrl(QUrl(self.page_url))
            return
        now = time.monotonic()
        self._plain_clicks = self._plain_clicks + 1 if now - self._last_click < 2.0 else 1
        self._last_click = now
        if self._plain_clicks >= self.PLAIN_CLICKS_FOR_TIP:
            self._plain_clicks = 0
            show_infobar("info", self.tr("Hold Ctrl to open the link"), self.link_hint, self.window())

    def reserve_progress_width(self, downloading: bool) -> None:
        """While downloading, keep the detail label as wide as the widest progress
        text, so the columns don't change width (and the description doesn't
        re-wrap, changing the card's height) with every progress update."""
        mb = format_bytes(1023.9 * 2**20)  # "1023.9 MB", the widest size format_bytes() writes
        widest = f"{mb} / {format_bytes(99.9 * 2**30)} · {mb}/s · " + self.tr("{m}:{s:02d} left").format(m=99, s=59)
        self.detail.setMinimumWidth(self.detail.fontMetrics().horizontalAdvance(widest) + 4 if downloading else 0)

    def set_progress(self, done: int, total: int, speed: float) -> None:
        self.progress.setValue(int(done * 1000 / total) if total else 0)
        eta = ""
        if speed > 0 and total > done:
            secs = int((total - done) / speed)
            eta = " · " + self.tr("{m}:{s:02d} left").format(m=secs // 60, s=secs % 60)
        self.detail.setText(f"{format_bytes(done)} / {format_bytes(total)} · {format_bytes(speed)}/s{eta}")


class ModelCard(_ItemCard):
    downloadClicked = Signal(str)
    cancelClicked = Signal(str)
    deleteClicked = Signal(str)  # delete the downloaded files
    removeClicked = Signal(str)  # take the model off the list
    useClicked = Signal(str)
    unloadClicked = Signal(str)

    def __init__(self, info: ModelInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self._has_files = False
        lay = self._build_frame()

        icon = IconWidget(FluentIcon.ROBOT, self)
        icon.setFixedSize(28, 28)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = StrongBodyLabel(info.name, self)
        text.addWidget(self.title)
        desc = CaptionLabel(info.description, self)
        desc.setWordWrap(True)
        text.addWidget(desc)
        # A model from a local folder is copied, not downloaded.
        template = (tr("Download {size} · {backend} · Languages: {langs} · License: {lic}") if info.repo else
                    tr("Size {size} · {backend} · Languages: {langs} · License: {lic}"))
        meta_text = template.format(size=format_bytes(info.size_mb * 2**20), backend=info.backend,
                                    langs=languages_text(info), lic=info.license)
        if info.custom:
            meta_text += " · " + tr("Added by you")
        meta = CaptionLabel(meta_text, self)
        meta.setWordWrap(True)
        text.addWidget(meta)
        lay.addLayout(text, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.status = BodyLabel("", self)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight)
        side.addWidget(self.status)
        self.detail = CaptionLabel("", self)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        side.addWidget(self.detail)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.use_btn = PushButton(self.tr("Use"), self)
        self.use_btn.clicked.connect(lambda: self.useClicked.emit(info.id))
        self.unload_btn = PushButton(self.tr("Unload"), self)
        self.unload_btn.setToolTip(self.tr("Free the memory the model uses. Captioning stops if it's running."))
        self.unload_btn.installEventFilter(ToolTipFilter(self.unload_btn))
        self.unload_btn.clicked.connect(lambda: self.unloadClicked.emit(info.id))
        self.download_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, self.tr("Download"), self)
        self.download_btn.clicked.connect(lambda: self.downloadClicked.emit(info.id))
        self.cancel_btn = PushButton(FluentIcon.CLOSE, self.tr("Cancel"), self)
        self.cancel_btn.clicked.connect(lambda: self.cancelClicked.emit(info.id))
        self.more_btn = TransparentToolButton(FluentIcon.MORE, self)
        self.more_btn.setToolTip(self.tr("More"))
        self.more_btn.installEventFilter(ToolTipFilter(self.more_btn))
        self.more_btn.clicked.connect(self._show_menu)
        for b in (self.use_btn, self.unload_btn, self.download_btn, self.cancel_btn, self.more_btn):
            buttons.addWidget(b)
        side.addLayout(buttons)
        side.addStretch(1)
        lay.addLayout(side)
        self._set_up_link_hint()

    @property
    def page_url(self) -> str:
        if self.info.repo:
            return f"https://huggingface.co/{self.info.repo}"
        return QUrl.fromLocalFile(str(model_path(self.info))).toString()  # a local model: open its folder

    @property
    def link_hint(self) -> str:
        if self.info.repo:
            return self.tr("Ctrl+click to open huggingface.co/{repo}").format(repo=self.info.repo)
        return self.tr("Ctrl+click to open the model's folder")

    def _show_menu(self) -> None:
        menu = RoundMenu(parent=self)
        menu.closedSignal.connect(menu.deleteLater)  # exec() doesn't wait for the menu to close
        # The actions open a dialog. They run once the menu is done with the click: the dialog's event loop
        # would delete the menu (deleteLater above) while its click handler is still running, and crash.
        delete = Action(FluentIcon.DELETE, self.tr("Delete files from disk"), menu)
        delete.setEnabled(self._has_files)
        delete.triggered.connect(lambda: QTimer.singleShot(0, lambda: self.deleteClicked.emit(self.info.id)))
        remove = Action(FluentIcon.REMOVE, self.tr("Remove from list"), menu)
        remove.triggered.connect(lambda: QTimer.singleShot(0, lambda: self.removeClicked.emit(self.info.id)))
        menu.addAction(delete)
        menu.addAction(remove)
        menu.exec(self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height())))

    def update_state(self, downloading: bool, in_use: bool, loading: bool = False) -> None:
        """``in_use``: the model is loaded, or (``loading``) being loaded; its files can't be deleted then."""
        info = self.info
        downloaded = is_downloaded(info)
        partial = has_partial_download(info)
        self.progress.setVisible(downloading)
        self.reserve_progress_width(downloading)
        self.cancel_btn.setVisible(downloading)
        self.download_btn.setVisible(not downloaded and not downloading)
        self.download_btn.setText(self.tr("Resume") if partial else self.tr("Download"))
        self.use_btn.setVisible(downloaded and not in_use)
        self.unload_btn.setVisible(in_use and not loading)
        self._has_files = downloaded or partial
        self.more_btn.setVisible(not downloading and not in_use)
        used = disk_usage(info) if (downloaded or partial) else 0
        if downloading:
            self.status.setText(self.tr("Downloading…"))
        elif in_use and loading:
            self.status.setText(self.tr("Loading…"))
        elif in_use:
            self.status.setText(self.tr("In use"))
        elif downloaded:
            self.status.setText(self.tr("Downloaded"))
        elif partial:
            self.status.setText(self.tr("Partly downloaded"))
        else:
            self.status.setText(self.tr("Not downloaded"))
        if not downloading:
            self.detail.setText(self.tr("On disk: {size}").format(size=format_bytes(used)) if used else "")


class GpuSupportCard(_ItemCard):
    """Downloads NVIDIA's CUDA libraries (``app/asr/cuda_libs.py``) so models can run on the GPU."""

    installed = Signal()  # a download finished; the libraries can be used now
    downloadEnded = Signal(bool)  # a download ended: True when it installed the libraries

    page_url = f"{LICENSES_URL}#nvidia-cuda-and-cudnn"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: CudaDownloadThread | None = None
        self.gpu_name = ""
        self.gpu: GpuInfo | None = None
        self._too_old = False  # no engine has GPU code for it: models run on the CPU
        self._legacy = False  # it needs the legacy engine (GTX 900 series)
        self.usable = False  # CTranslate2 can use the GPU
        self.link_hint = self.tr("Ctrl+click to open the license of NVIDIA's libraries")
        lay = self._build_frame()

        icon = IconWidget(FluentIcon.SPEED_HIGH, self)
        icon.setFixedSize(28, 28)
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(StrongBodyLabel(self.tr("NVIDIA GPU support"), self))
        self.desc = CaptionLabel("", self)
        self.desc.setWordWrap(True)
        text.addWidget(self.desc)
        self.meta = CaptionLabel("", self)
        self.meta.setWordWrap(True)
        text.addWidget(self.meta)
        lay.addLayout(text, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        self.status = BodyLabel("", self)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight)
        side.addWidget(self.status)
        self.detail = CaptionLabel("", self)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight)
        side.addWidget(self.detail)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.download_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, self.tr("Download"), self)
        self.download_btn.clicked.connect(self.start_download)
        self.cancel_btn = PushButton(FluentIcon.CLOSE, self.tr("Cancel"), self)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.keep_btn = PushButton(self.tr("Keep"), self)
        self.keep_btn.clicked.connect(self._keep)
        self.remove_btn = TransparentToolButton(FluentIcon.DELETE, self)
        self.remove_btn.setToolTip(self.tr("Delete from disk"))
        self.remove_btn.installEventFilter(ToolTipFilter(self.remove_btn))
        self.remove_btn.clicked.connect(self._remove)
        for b in (self.download_btn, self.cancel_btn, self.keep_btn, self.remove_btn):
            buttons.addWidget(b)
        side.addLayout(buttons)
        side.addStretch(1)
        lay.addLayout(side)
        self._set_up_link_hint()
        self.set_gpu(None, False)

    @property
    def downloading(self) -> bool:
        return self._thread is not None

    @staticmethod
    def _libraries_in_use() -> bool:
        """This process has the downloaded libraries on its DLL path (and may have loaded them)."""
        return str(cuda_libs.lib_dir()) in active_dirs()

    def _in_use(self) -> bool:
        """Deleting must wait for the next start: Windows won't delete loaded DLLs (the downloaded libraries, or
        the legacy engine this process runs on)."""
        return self._libraries_in_use() or legacy_engine_loaded()

    def set_gpu(self, name: str | None, usable: bool) -> None:
        self.gpu_name = name or ""
        self.gpu = nvidia_gpu() if name else None
        self.usable = usable
        self._too_old = too_old_for_gpu_support(self.gpu)
        self._legacy = self.gpu is not None and wants_legacy_engine(self.gpu)
        if self._too_old:
            desc = self.tr("Your {gpu} is too old for GPU support: it needs a GeForce GTX 900 series or newer NVIDIA "
                           "GPU. Models run on the CPU.").format(gpu=name)
        else:
            desc = (self.tr("Runs models on your {gpu} with CUDA, many times faster than on the CPU.").format(gpu=name)
                    if name else self.tr("Runs models on an NVIDIA GPU with CUDA, many times faster than on the CPU.")
                    ) + " " + self.tr("Downloads NVIDIA's CUDA libraries once; the app doesn't include them.")
            if self._legacy:
                desc += " " + self.tr("Your GPU is an older one, so this also downloads an engine built for it.")
        self.desc.setText(desc)
        meta = self.tr("Download {download} · {installed} on disk · cuBLAS 12.4 and cuDNN 9.1 from PyPI · "
                       "License: NVIDIA Software License").format(
            download=format_bytes(cuda_libs.DOWNLOAD_SIZE), installed=format_bytes(cuda_libs.INSTALLED_SIZE))
        if self._legacy:
            meta += " · " + self.tr("Engine for older GPUs: {size}, CTranslate2 (MIT)").format(
                size=format_bytes(cuda_libs.LEGACY_ENGINE.size))
        self.meta.setText(meta)
        self.update_state()

    def worth_showing(self) -> bool:
        """There is an NVIDIA GPU, or files from an earlier download to manage."""
        return bool(self.gpu_name) or self.downloading or cuda_libs.disk_usage() > 0

    def update_state(self) -> None:
        downloading = self.downloading
        installed = cuda_libs.installed_dir() is not None
        scheduled = not downloading and cuda_libs.removal_scheduled()
        partial = not downloading and cuda_libs.has_partial_download()
        elsewhere = not installed and not scheduled and not self._libraries_in_use() and cuda_libraries_found()
        too_old, legacy = self._too_old, self._legacy
        engine = cuda_libs.legacy_dll() is not None  # (the engine is downloaded)
        # The CUDA libraries are there, but the engine for this older GPU isn't (yet), or it is but runs after a restart.
        engine_missing = legacy and (installed or elsewhere) and not scheduled and not engine
        restart = legacy and not scheduled and engine and not legacy_engine_loaded()
        self.progress.setVisible(downloading)
        self.reserve_progress_width(downloading)
        self.cancel_btn.setVisible(downloading)
        self.download_btn.setVisible(not (downloading or scheduled or too_old)
                                     and (engine_missing or not (installed or elsewhere)))
        self.download_btn.setText(self.tr("Resume") if partial else self.tr("Download"))
        self.keep_btn.setVisible(scheduled)
        self.remove_btn.setVisible(not downloading and (installed or partial or engine))
        if downloading:
            self.status.setText(self.tr("Downloading…"))
        elif scheduled:
            self.status.setText(self.tr("Deleted when the app restarts"))
        elif engine_missing:
            self.status.setText(self.tr("Needs the engine for older GPUs"))
        elif installed or (elsewhere and restart):
            self.status.setText(self.tr("Installed") if self.usable or restart or not self.gpu_name or too_old
                                else self.tr("Installed, but the GPU can't be used"))
        elif elsewhere:
            self.status.setText(self.tr("Found on this PC"))
        elif partial:
            self.status.setText(self.tr("Partly downloaded"))
        elif too_old:
            self.status.setText(self.tr("Not supported on this GPU"))
        else:
            self.status.setText(self.tr("Not installed"))
        if not downloading:
            if restart:
                self.detail.setText(self.tr("Restart the app to use the GPU"))
            elif engine_missing:
                self.detail.setText(self.tr("Download {size}").format(size=format_bytes(cuda_libs.LEGACY_ENGINE.size)))
            elif elsewhere:
                self.detail.setText(active_dirs()[0])
            elif installed and self.gpu_name and not self.usable and not too_old:
                self.detail.setText(self.tr("Update the NVIDIA driver, then restart the app"))
            else:
                used = cuda_libs.disk_usage()
                self.detail.setText(self.tr("On disk: {size}").format(size=format_bytes(used)) if used else "")

    def start_download(self) -> None:
        if self._thread is not None:
            return
        self._thread = t = CudaDownloadThread(self)
        t.progress.connect(lambda p: self.set_progress(p.done, p.total, p.speed))
        t.done.connect(self._on_done)
        self.progress.setValue(0)
        self.detail.setText("")
        t.start()
        self.update_state()

    def cancel_download(self) -> None:
        if self._thread is not None:
            self._thread.cancel_event.set()

    def shutdown(self) -> None:
        if self._thread is not None:
            self._thread.cancel_event.set()
            self._thread.wait(5000)

    def _on_done(self, result: str, message: str) -> None:
        t, self._thread = self._thread, None
        if t is not None:
            t.wait(1000)
            t.deleteLater()
        self.update_state()
        self.downloadEnded.emit(result == "ok")
        if result == "ok":
            self.installed.emit()
        elif result != "cancelled":
            show_infobar("error", self.tr("Download failed: NVIDIA GPU support"),
                         cuda_download_error_text(result, message), self.window(), duration=12000)

    def _remove(self) -> None:
        in_use = self._in_use()
        body = self.tr("Delete NVIDIA's CUDA libraries ({size}) from disk? Models then run on the CPU. "
                       "You can download them again later.").format(size=format_bytes(cuda_libs.disk_usage()))
        if in_use:
            body += " " + self.tr("They're in use, so they're deleted the next time the app starts.")
        box = MessageBox(self.tr("Remove GPU support?"), body, self.window())
        box.yesButton.setText(self.tr("Delete"))
        box.cancelButton.setText(self.tr("Cancel"))
        if not box.exec():
            return
        cuda_libs.remove(in_use)
        self.update_state()

    def _keep(self) -> None:
        cuda_libs.cancel_removal()
        self.update_state()


class ModelsPage(QWidget):
    useRequested = Signal(str)
    unloadRequested = Signal(str)
    downloadRequested = Signal(str)
    modelsChanged = Signal()
    catalogChanged = Signal()  # models were added, removed or restored
    downloadFinished = Signal(str, bool)
    gpuSupportInstalled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelsPage")
        self.current_model_id: str | None = None
        self.current_loading = False  # current_model_id is still being loaded
        self.downloads = DownloadManager(self)
        self.downloads.progress.connect(self._on_progress)
        self.downloads.finished.connect(self._on_finished)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none}")
        body = QWidget()
        body.setStyleSheet("background:transparent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(36, 24, 36, 24)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(TitleLabel(self.tr("Models"), body))
        head.addStretch(1)
        self.add_btn = PushButton(FluentIcon.ADD, self.tr("Add model"), body)
        self.add_btn.clicked.connect(self.add_model)
        head.addWidget(self.add_btn)
        lay.addLayout(head)
        self.folder_label = CaptionLabel("", body)
        lay.addWidget(self.folder_label)
        self.gpu_card = GpuSupportCard(body)
        self.gpu_card.installed.connect(self.gpuSupportInstalled)
        lay.addWidget(self.gpu_card)
        self.cards: dict[str, ModelCard] = {}
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(10)
        lay.addLayout(self.cards_layout)

        # Shown instead of the cards when every model has been removed.
        self.empty = QWidget(body)
        empty_lay = QVBoxLayout(self.empty)
        empty_lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        empty_lay.setSpacing(8)
        empty_lay.setContentsMargins(0, 36, 0, 24)
        empty_icon = IconWidget(FluentIcon.LIBRARY, self.empty)
        empty_icon.setFixedSize(48, 48)
        empty_lay.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_lay.addWidget(StrongBodyLabel(self.tr("No models in the list"), self.empty), 0, Qt.AlignmentFlag.AlignHCenter)
        empty_lay.addWidget(CaptionLabel(self.tr("Bring back the preset models, or add your own."), self.empty),
                            0, Qt.AlignmentFlag.AlignHCenter)
        self.restore_btn = PrimaryPushButton(FluentIcon.SYNC, self.tr("Restore default models"), self.empty)
        self.restore_btn.clicked.connect(lambda: self.reset_catalog(delete_files=False))
        empty_lay.addSpacing(8)
        empty_lay.addWidget(self.restore_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.empty)

        lay.addStretch(1)
        scroll.setWidget(body)
        # A tooltip only hides when the mouse leaves its widget, and scrolling moves the widget away
        # without that happening: the tooltip would stay behind on the screen.
        scroll.verticalScrollBar().valueChanged.connect(self._hide_tooltips)
        outer.addWidget(scroll)
        self.sync_cards()
        self.refresh()

    def _hide_tooltips(self) -> None:
        for f in self.findChildren(ToolTipFilter):
            f.hideToolTip()

    def sync_cards(self) -> None:
        """Make the cards match the model list (after models were added, removed or restored)."""
        body = self.scroll.widget()
        models = load_catalog()
        for mid in [m for m in self.cards if m not in {i.id for i in models}]:
            self._drop_card(mid)
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)  # re-added below in the list's order
        for info in models:
            card = self.cards.get(info.id)
            if card is not None and card.info != info:
                self._drop_card(info.id)
                card = None
            if card is None:
                card = ModelCard(info, body)
                card.downloadClicked.connect(self.downloadRequested)
                card.cancelClicked.connect(self.downloads.cancel)
                card.deleteClicked.connect(self._delete)
                card.removeClicked.connect(self._remove)
                card.useClicked.connect(self.useRequested)
                card.unloadClicked.connect(self.unloadRequested)
                self.cards[info.id] = card
            self.cards_layout.addWidget(card)
            card.show()
        self.empty.setVisible(not models)

    def _drop_card(self, model_id: str) -> None:
        card = self.cards.pop(model_id)
        card.hide()
        card.setParent(None)
        card.deleteLater()

    def _catalog_changed(self) -> None:
        self.sync_cards()
        self.refresh()
        self.catalogChanged.emit()

    # -- adding, removing, restoring --------------------------------------------------
    def add_model(self) -> None:
        dialog = AddModelDialog(self.window())
        if not dialog.exec() or dialog.entry is None:
            return
        listed = {m.id for m in load_catalog()}
        info = add_custom_model(dialog.entry)
        if info.id in listed:  # its repository is a model's in the list already: nothing was added
            show_infobar("info", self.tr("Already in the list"),
                         self.tr("{name} is in the list already.").format(name=info.name), self.window())
        else:
            self._catalog_changed()
            if info.source_folder:
                # Copy it into the models folder right away. (Not through downloadRequested: the main
                # window would then switch to it when the copy is done, replacing the model in use.)
                self.start_download(info)
            elif is_downloaded(info):  # a removed preset whose files were kept
                show_infobar("success", self.tr("Model added"),
                             self.tr("{name} is in the list, and downloaded already.").format(name=info.name),
                             self.window())
            else:
                show_infobar("success", self.tr("Model added"),
                             self.tr("{name} is in the list. Download it when you want to use it.").format(
                                 name=info.name), self.window())
        self.show_model(info.id)

    def _remove(self, model_id: str) -> None:
        card = self.cards[model_id]
        info = card.info
        files = disk_usage(info)
        if info.custom:
            content = self.tr("{name} is removed from the list. You can add it again later.").format(name=info.name)
        else:
            content = self.tr("{name} is removed from the list. Reset the model list in Settings (or press "
                              "Restore default models when the list is empty) to bring it back.").format(name=info.name)
        dialog = ConfirmDialog(self.tr("Remove from the list?"), content, self.tr("Remove"),
                               option=(self.tr("Also delete the downloaded files ({size})").format(size=format_bytes(files))
                                       if files else ""), parent=self.window())
        if not dialog.exec():
            return
        remove_model(model_id)
        if dialog.option_checked:
            delete_model(info)
        self._catalog_changed()

    def reset_catalog(self, delete_files: bool) -> None:
        """Back to the preset list; the models the user added are dropped (and their files deleted if asked)."""
        for info in reset_catalog():
            if delete_files:
                delete_model(info)
        self._catalog_changed()

    def show_model(self, model_id: str) -> None:
        """Scroll ``model_id``'s card into view and highlight it."""
        card = self.cards.get(model_id)
        if card is None:
            return
        self.refresh()
        # Wait for the page to be laid out after switching to it.
        QTimer.singleShot(50, lambda: (self.scroll.ensureWidgetVisible(card, 0, 24), card.flash()))

    def set_gpu(self, name: str | None, usable: bool) -> None:
        """The NVIDIA GPU's name (None: no NVIDIA GPU) and whether models can run on it."""
        self.gpu_card.set_gpu(name, usable)
        self.refresh()

    def refresh(self) -> None:
        self.folder_label.setText(self.tr("Models are stored in {path}").format(path=models_dir()))
        self.empty.setVisible(not self.cards)
        self.gpu_card.setVisible(self.gpu_card.worth_showing())
        self.gpu_card.update_state()
        for mid, card in self.cards.items():
            card.update_state(self.downloads.is_active(mid), mid == self.current_model_id, self.current_loading)

    def set_current_model(self, model_id: str | None, loading: bool = False) -> None:
        """The model that is loaded, or being loaded (``loading``); None when there is none."""
        self.current_model_id = model_id
        self.current_loading = loading
        self.refresh()

    def start_download(self, info: ModelInfo) -> None:
        self.downloads.start(info)
        self.refresh()

    def _on_progress(self, model_id: str, p) -> None:
        card = self.cards.get(model_id)
        if card:
            card.set_progress(p.done, p.total, p.speed)

    def _on_finished(self, model_id: str, result: str, message: str) -> None:
        card = self.cards.get(model_id)
        name = card.info.name if card else model_id
        self.refresh()
        self.modelsChanged.emit()
        if result == "ok":
            show_infobar("success", self.tr("Download complete"), self.tr("{name} is ready.").format(name=name),
                         self.window())
        elif result != "cancelled":
            show_infobar("error", self.tr("Download failed: {name}").format(name=name),
                         download_error_text(result, message), self.window(), duration=12000)
        self.downloadFinished.emit(model_id, result == "ok")

    def _delete(self, model_id: str) -> None:
        card = self.cards[model_id]
        info = card.info
        if not info.source_folder:
            content = self.tr("Delete {name} from disk? You can download it again later.")
        elif Path(info.source_folder).is_dir():
            content = self.tr("Delete {name} from disk? It can be copied again from {folder} later, as long as "
                              "that folder is there.")
        else:
            content = self.tr("Delete {name} from disk? The folder it was added from ({folder}) is gone, so it "
                              "can't be copied again: add the model again from wherever it is now.")
        box = MessageBox(self.tr("Delete model?"), content.format(name=info.name, folder=info.source_folder),
                         self.window())
        box.yesButton.setText(self.tr("Delete"))
        box.cancelButton.setText(self.tr("Cancel"))
        if box.exec():
            delete_model(card.info)
            self.refresh()
            self.modelsChanged.emit()

    def shutdown(self) -> None:
        self.downloads.cancel_all()
        self.gpu_card.shutdown()
