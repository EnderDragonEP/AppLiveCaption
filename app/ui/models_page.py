"""Models page: GPU support and every catalog model, with download/delete, disk usage and license."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    IconWidget,
    MessageBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    StrongBodyLabel,
    TitleLabel,
    ToolTipFilter,
    TransparentToolButton,
    themeColor,
)

from .. import LICENSES_URL
from ..asr import cuda_libs
from ..asr.cuda_setup import active_dirs, cuda_libraries_found
from ..asr.languages import language_name
from ..config import cfg
from ..i18n import tr
from ..models.catalog import ModelInfo, load_catalog
from ..models.storage import delete_model, disk_usage, has_partial_download, is_downloaded, models_dir
from .download import CudaDownloadThread, DownloadManager, cuda_download_error_text, download_error_text, format_bytes
from .messages import show_infobar


def _languages_text(info: ModelInfo) -> str:
    if len(info.languages) > 10:
        return tr("{n} languages").format(n=len(info.languages))
    return ", ".join(info.language_label(c) if c in info.language_notes else language_name(c) for c in info.languages)


class _ItemCard(CardWidget):
    """A card that opens ``page_url`` when clicked and can be highlighted for a moment.

    Subclasses call ``_build_frame`` and add ``detail`` (a label) for ``set_progress``.
    """

    page_url = ""

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

    def mouseReleaseEvent(self, e):  # noqa: N802 - Qt override
        super().mouseReleaseEvent(e)
        # Buttons on the card handle their own clicks; this is a click on the
        # card itself. Ignore right-clicks and drags released outside the card.
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self.page_url))

    def flash(self) -> None:
        """Outline the card in the accent color for a moment, then fade it out."""
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(1600)
        anim.valueChanged.connect(self._set_highlight)
        self._highlight_anim = anim  # keep a reference while it runs
        self._set_highlight(1.0)
        QTimer.singleShot(900, anim.start)  # hold fully visible first

    def _set_highlight(self, value) -> None:
        self._highlight = float(value)
        self.update()

    def paintEvent(self, e):  # noqa: N802 - Qt override
        super().paintEvent(e)
        if getattr(self, "_highlight", 0.0) <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(themeColor())
        color.setAlphaF(self._highlight)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)

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
    deleteClicked = Signal(str)
    useClicked = Signal(str)

    def __init__(self, info: ModelInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        meta = CaptionLabel(
            tr("Download {size} · {backend} · Languages: {langs} · License: {lic}").format(
                size=format_bytes(info.size_mb * 2**20), backend=info.backend, langs=_languages_text(info),
                lic=info.license),
            self,
        )
        meta.setWordWrap(True)
        meta.setToolTip(info.repo)
        meta.installEventFilter(ToolTipFilter(meta))
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
        self.download_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, self.tr("Download"), self)
        self.download_btn.clicked.connect(lambda: self.downloadClicked.emit(info.id))
        self.cancel_btn = PushButton(FluentIcon.CLOSE, self.tr("Cancel"), self)
        self.cancel_btn.clicked.connect(lambda: self.cancelClicked.emit(info.id))
        self.delete_btn = TransparentToolButton(FluentIcon.DELETE, self)
        self.delete_btn.setToolTip(self.tr("Delete from disk"))
        self.delete_btn.installEventFilter(ToolTipFilter(self.delete_btn))
        self.delete_btn.clicked.connect(lambda: self.deleteClicked.emit(info.id))
        for b in (self.use_btn, self.download_btn, self.cancel_btn, self.delete_btn):
            buttons.addWidget(b)
        side.addLayout(buttons)
        side.addStretch(1)
        lay.addLayout(side)

    @property
    def page_url(self) -> str:
        return f"https://huggingface.co/{self.info.repo}"

    def update_state(self, downloading: bool, in_use: bool) -> None:
        info = self.info
        downloaded = is_downloaded(info)
        partial = has_partial_download(info)
        self.progress.setVisible(downloading)
        self.reserve_progress_width(downloading)
        self.cancel_btn.setVisible(downloading)
        self.download_btn.setVisible(not downloaded and not downloading)
        self.download_btn.setText(self.tr("Resume") if partial else self.tr("Download"))
        self.use_btn.setVisible(downloaded and not in_use)
        self.delete_btn.setVisible((downloaded or partial) and not downloading and not in_use)
        used = disk_usage(info) if (downloaded or partial) else 0
        if downloading:
            self.status.setText(self.tr("Downloading…"))
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

    page_url = f"{LICENSES_URL}#nvidia-cuda-and-cudnn"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: CudaDownloadThread | None = None
        self.gpu_name = ""
        self.usable = False  # CTranslate2 can use the GPU
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        meta = CaptionLabel(
            self.tr("Download {download} · {installed} on disk · cuBLAS 12.4 and cuDNN 9.1 from PyPI · "
                    "License: NVIDIA Software License").format(
                download=format_bytes(cuda_libs.DOWNLOAD_SIZE), installed=format_bytes(cuda_libs.INSTALLED_SIZE)),
            self,
        )
        meta.setWordWrap(True)
        meta.setToolTip(self.page_url)
        meta.installEventFilter(ToolTipFilter(meta))
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
        self.set_gpu(None, False)

    @property
    def downloading(self) -> bool:
        return self._thread is not None

    @staticmethod
    def _in_use() -> bool:
        """This process has the downloaded libraries on its DLL path (and may have loaded them)."""
        return str(cuda_libs.lib_dir()) in active_dirs()

    def set_gpu(self, name: str | None, usable: bool) -> None:
        self.gpu_name = name or ""
        self.usable = usable
        self.desc.setText(
            (self.tr("Runs models on your {gpu} with CUDA, many times faster than on the CPU.").format(gpu=name)
             if name else self.tr("Runs models on an NVIDIA GPU with CUDA, many times faster than on the CPU."))
            + " " + self.tr("Downloads NVIDIA's CUDA libraries once; the app doesn't include them."))
        self.update_state()

    def worth_showing(self) -> bool:
        """There is an NVIDIA GPU, or files from an earlier download to manage."""
        return bool(self.gpu_name) or self.downloading or cuda_libs.disk_usage() > 0

    def update_state(self) -> None:
        downloading = self.downloading
        installed = cuda_libs.installed_dir() is not None
        scheduled = not downloading and cuda_libs.removal_scheduled()
        partial = not downloading and cuda_libs.has_partial_download()
        elsewhere = not installed and not scheduled and not self._in_use() and cuda_libraries_found()
        self.progress.setVisible(downloading)
        self.reserve_progress_width(downloading)
        self.cancel_btn.setVisible(downloading)
        self.download_btn.setVisible(not (downloading or installed or scheduled or elsewhere))
        self.download_btn.setText(self.tr("Resume") if partial else self.tr("Download"))
        self.keep_btn.setVisible(scheduled)
        self.remove_btn.setVisible(not downloading and (installed or partial))
        if downloading:
            self.status.setText(self.tr("Downloading…"))
        elif scheduled:
            self.status.setText(self.tr("Deleted when the app restarts"))
        elif installed:
            self.status.setText(self.tr("Installed") if self.usable or not self.gpu_name
                                else self.tr("Installed, but the GPU can't be used"))
        elif elsewhere:
            self.status.setText(self.tr("Found on this PC"))
        elif partial:
            self.status.setText(self.tr("Partly downloaded"))
        else:
            self.status.setText(self.tr("Not installed"))
        if not downloading:
            if elsewhere:
                self.detail.setText(active_dirs()[0])
            elif installed and self.gpu_name and not self.usable:
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
        if cuda_libs.installed_dir() is not None:
            cfg.set(cfg.offerGpuSupport, False)  # removed on purpose: don't suggest it again at start-up
        cuda_libs.remove(in_use)
        self.update_state()

    def _keep(self) -> None:
        cuda_libs.cancel_removal()
        self.update_state()


class ModelsPage(QWidget):
    useRequested = Signal(str)
    downloadRequested = Signal(str)
    modelsChanged = Signal()
    downloadFinished = Signal(str, bool)
    gpuSupportInstalled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelsPage")
        self.current_model_id: str | None = None
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
        lay.addWidget(TitleLabel(self.tr("Models"), body))
        self.folder_label = CaptionLabel("", body)
        lay.addWidget(self.folder_label)
        self.gpu_card = GpuSupportCard(body)
        self.gpu_card.installed.connect(self.gpuSupportInstalled)
        lay.addWidget(self.gpu_card)
        self.cards: dict[str, ModelCard] = {}
        for info in load_catalog():
            card = ModelCard(info, body)
            card.downloadClicked.connect(self.downloadRequested)
            card.cancelClicked.connect(self.downloads.cancel)
            card.deleteClicked.connect(self._delete)
            card.useClicked.connect(self.useRequested)
            self.cards[info.id] = card
            lay.addWidget(card)
        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        self.refresh()

    def show_model(self, model_id: str) -> None:
        """Scroll ``model_id``'s card into view and highlight it."""
        card = self.cards.get(model_id)
        if card is None:
            return
        self.refresh()
        # Wait for the page to be laid out after switching to it.
        QTimer.singleShot(50, lambda: (self.scroll.ensureWidgetVisible(card, 0, 24), card.flash()))

    def show_gpu_support(self) -> None:
        """Scroll the GPU support card into view and highlight it."""
        self.refresh()
        card = self.gpu_card
        QTimer.singleShot(50, lambda: (self.scroll.ensureWidgetVisible(card, 0, 24), card.flash()))

    def set_gpu(self, name: str | None, usable: bool) -> None:
        """The NVIDIA GPU's name (None: no NVIDIA GPU) and whether models can run on it."""
        self.gpu_card.set_gpu(name, usable)
        self.refresh()

    def refresh(self) -> None:
        self.folder_label.setText(self.tr("Models are stored in {path}").format(path=models_dir()))
        self.gpu_card.setVisible(self.gpu_card.worth_showing())
        self.gpu_card.update_state()
        for mid, card in self.cards.items():
            card.update_state(self.downloads.is_active(mid), mid == self.current_model_id)

    def set_current_model(self, model_id: str | None) -> None:
        self.current_model_id = model_id
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
        box = MessageBox(self.tr("Delete model?"),
                         self.tr("Delete {name} from disk? You can download it again later.").format(name=card.info.name),
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
