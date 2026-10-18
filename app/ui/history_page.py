"""History page: past transcripts with copy, TXT/SRT export and clearing."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidgetItem, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    ListWidget,
    MessageBox,
    PlainTextEdit,
    PushButton,
    TitleLabel,
)

from ..core.history import HistorySession, HistoryStore
from .messages import show_infobar


class HistoryPage(QWidget):
    def __init__(self, store: HistoryStore, parent=None):
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store
        self.sessions: list[HistorySession] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 24)
        root.setSpacing(12)
        root.addWidget(TitleLabel(self.tr("History"), self))

        bar = QHBoxLayout()
        self.copy_btn = PushButton(FluentIcon.COPY, self.tr("Copy"), self)
        self.copy_btn.clicked.connect(self._copy)
        self.txt_btn = PushButton(FluentIcon.DOCUMENT, self.tr("Export TXT"), self)
        self.txt_btn.clicked.connect(lambda: self._export("txt"))
        self.srt_btn = PushButton(FluentIcon.VIDEO, self.tr("Export SRT"), self)
        self.srt_btn.clicked.connect(lambda: self._export("srt"))
        self.delete_btn = PushButton(FluentIcon.DELETE, self.tr("Delete"), self)
        self.delete_btn.clicked.connect(self._delete)
        self.clear_btn = PushButton(FluentIcon.BROOM, self.tr("Clear all"), self)
        self.clear_btn.clicked.connect(self._clear)
        for b in (self.copy_btn, self.txt_btn, self.srt_btn, self.delete_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.clear_btn)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        # Qt paints the handle in its own (dark) palette color, which shows up as a
        # dark bar beside the transcript box. Keep it as an invisible gap instead.
        split.setHandleWidth(12)
        split.setStyleSheet("QSplitter::handle { background: transparent; }")
        self.list = ListWidget(split)
        self.list.setMinimumWidth(260)
        self.list.currentRowChanged.connect(self._show)
        self.text = PlainTextEdit(split)
        self.text.setReadOnly(True)
        split.addWidget(self.list)
        split.addWidget(self.text)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 700])
        root.addWidget(split, 1)
        self.empty_label = CaptionLabel(self.tr("Transcripts of your captioning sessions appear here."), self)
        root.addWidget(self.empty_label)
        self.reload()

    def reload(self) -> None:
        current = self._current().id if self._current() else None
        self.sessions = self.store.list_sessions()
        self.list.blockSignals(True)
        self.list.clear()
        row = 0
        for i, s in enumerate(self.sessions):
            mins = int(s.duration // 60)
            label = self.tr("{title}  ·  {n} lines  ·  {m} min").format(title=s.title, n=len(s.lines), m=mins)
            item = QListWidgetItem(label)
            item.setToolTip(f"{s.model}\n{s.source}")
            self.list.addItem(item)
            if s.id == current:
                row = i
        self.list.blockSignals(False)
        has = bool(self.sessions)
        self.empty_label.setVisible(not has)
        for b in (self.copy_btn, self.txt_btn, self.srt_btn, self.delete_btn, self.clear_btn):
            b.setEnabled(has)
        if has:
            self.list.setCurrentRow(row)
            self._show(row)
        else:
            self.text.clear()

    def _current(self) -> HistorySession | None:
        row = self.list.currentRow() if hasattr(self, "list") else -1
        return self.sessions[row] if 0 <= row < len(self.sessions) else None

    def _show(self, row: int) -> None:
        if 0 <= row < len(self.sessions):
            s = self.sessions[row]
            header = f"{s.title}  ·  {s.model}  ·  {s.source}\n\n"
            self.text.setPlainText(header + s.to_txt())

    def _copy(self) -> None:
        s = self._current()
        if s:
            QGuiApplication.clipboard().setText(s.to_txt())
            show_infobar("success", self.tr("Copied"), self.tr("The transcript is on the clipboard."), self.window(), 2500)

    def _export(self, fmt: str) -> None:
        s = self._current()
        if not s:
            return
        default = f"captions-{datetime.fromtimestamp(s.started):%Y%m%d-%H%M}.{fmt}"
        filt = self.tr("Text files (*.txt)") if fmt == "txt" else self.tr("Subtitles (*.srt)")
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Export transcript"), default, filt)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig" if fmt == "txt" else "utf-8", newline="\n") as fh:
                fh.write(s.to_txt() if fmt == "txt" else s.to_srt())
        except OSError as exc:
            show_infobar("error", self.tr("Export failed"), str(exc), self.window())
            return
        show_infobar("success", self.tr("Exported"), path, self.window(), 3000)

    def _delete(self) -> None:
        s = self._current()
        if s:
            self.store.delete(s.id)
            self.reload()

    def _clear(self) -> None:
        box = MessageBox(self.tr("Clear history?"), self.tr("Delete all saved transcripts? This can't be undone."),
                         self.window())
        box.yesButton.setText(self.tr("Clear all"))
        box.cancelButton.setText(self.tr("Cancel"))
        if box.exec():
            self.store.clear()
            self.reload()
