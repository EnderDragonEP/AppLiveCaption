"""Models page: every catalog model with download/delete, disk usage and license."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
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

from ..asr.languages import language_name
from ..i18n import tr
from ..models.catalog import ModelInfo, load_catalog
from ..models.storage import delete_model, disk_usage, has_partial_download, is_downloaded, models_dir
from .download import DownloadManager, download_error_text, format_bytes
from .messages import show_infobar


def _languages_text(info: ModelInfo) -> str:
    if len(info.languages) > 10:
        return tr("{n} languages").format(n=len(info.languages))
    return ", ".join(info.language_label(c) if c in info.language_notes else language_name(c) for c in info.languages)


class ModelCard(CardWidget):
    downloadClicked = Signal(str)
    cancelClicked = Signal(str)
    deleteClicked = Signal(str)
    useClicked = Signal(str)

    def __init__(self, info: ModelInfo, parent=None):
        super().__init__(parent)
        self.info = info
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 14, 16, 14)
        lay.setSpacing(16)

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
        self.progress = ProgressBar(self)
        self.progress.setRange(0, 1000)
        self.progress.hide()
        text.addWidget(self.progress)
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

    def update_state(self, downloading: bool, in_use: bool) -> None:
        info = self.info
        downloaded = is_downloaded(info)
        partial = has_partial_download(info)
        self.progress.setVisible(downloading)
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

    def set_progress(self, done: int, total: int, speed: float) -> None:
        self.progress.setValue(int(done * 1000 / total) if total else 0)
        eta = ""
        if speed > 0 and total > done:
            secs = int((total - done) / speed)
            eta = " · " + self.tr("{m}:{s:02d} left").format(m=secs // 60, s=secs % 60)
        self.detail.setText(f"{format_bytes(done)} / {format_bytes(total)} · {format_bytes(speed)}/s{eta}")


class ModelsPage(QWidget):
    useRequested = Signal(str)
    downloadRequested = Signal(str)
    modelsChanged = Signal()
    downloadFinished = Signal(str, bool)

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

    def refresh(self) -> None:
        self.folder_label.setText(self.tr("Models are stored in {path}").format(path=models_dir()))
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
