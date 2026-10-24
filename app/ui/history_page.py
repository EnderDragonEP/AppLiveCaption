"""History page: past transcripts with copy, TXT/SRT export and clearing."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidgetItem, QSplitter, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    ListWidget,
    MessageBox,
    PlainTextEdit,
    PushButton,
    TitleLabel,
    ToolTipFilter,
)

from ..config import cfg
from ..core.history import HistorySession, HistoryStore
from ..core.keywords import utf16_spans
from .messages import show_infobar


class HistoryPage(QWidget):
    # Refresh (button or F5): save the session being captioned, then reload().
    refreshRequested = Signal()

    def __init__(self, store: HistoryStore, keywords=None, parent=None):
        """``keywords``: the KeywordService, whose keywords are highlighted in the transcript."""
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store
        self.keywords = keywords
        self.sessions: list[HistorySession] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 24)
        root.setSpacing(12)
        root.addWidget(TitleLabel(self.tr("History"), self))

        bar = QHBoxLayout()
        self.refresh_btn = PushButton(FluentIcon.SYNC, self.tr("Refresh"), self)
        self.refresh_btn.setToolTip(self.tr("Show the latest lines (F5)"))
        self.refresh_btn.installEventFilter(ToolTipFilter(self.refresh_btn))
        self.refresh_btn.clicked.connect(self.refreshRequested)
        QShortcut(QKeySequence.StandardKey.Refresh, self, self.refreshRequested.emit)
        bar.addWidget(self.refresh_btn)
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
        has = bool(self.sessions)
        if has:
            self.list.setCurrentRow(row)  # signals still blocked: _show() below decides about scrolling
        self.list.blockSignals(False)
        self.empty_label.setVisible(not has)
        for b in (self.copy_btn, self.txt_btn, self.srt_btn, self.delete_btn, self.clear_btn):
            b.setEnabled(has)
        if has:
            self._show(row, keep_scroll=self.sessions[row].id == current)
        else:
            self.text.clear()

    def _current(self) -> HistorySession | None:
        row = self.list.currentRow() if hasattr(self, "list") else -1
        return self.sessions[row] if 0 <= row < len(self.sessions) else None

    def _show(self, row: int, keep_scroll: bool = False) -> None:
        if not 0 <= row < len(self.sessions):
            return
        s = self.sessions[row]
        text = f"{s.title}  ·  {s.model}  ·  {s.source}\n\n{s.to_txt()}".rstrip("\n")
        shown = self.text.toPlainText()
        if keep_scroll and shown and text.startswith(shown + "\n"):
            # The same transcript with new lines: appending keeps the reader's
            # place, or follows the end if they were at the bottom.
            old_blocks = self.text.document().blockCount()
            self.text.appendPlainText(text[len(shown) + 1:])
            self.mark_keywords(from_block=old_blocks)  # only the new lines
        elif not keep_scroll or text != shown:
            self.text.setPlainText(text)
            self.mark_keywords()

    def mark_keywords(self, from_block: int = 0) -> None:
        """Highlight the keywords in the transcript (Keywords page), from block ``from_block`` on."""
        # The text box's style sheet sets the text color, and Qt lets that win over a word's own color, so the
        # mark is the highlight color made see-through, and bold: readable with white (dark theme) or black text.
        doc = self.text.document()
        doc.setUndoRedoEnabled(False)
        first = doc.findBlockByNumber(from_block)
        if not first.isValid():
            return
        cursor = QTextCursor(doc)
        cursor.setPosition(first.position())
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.setCharFormat(QTextCharFormat())  # earlier marks (the keywords may have changed)
        if self.keywords is None or not cfg.get(cfg.keywordHighlight):
            return
        color = QColor(cfg.get(cfg.keywordColor))
        color.setAlpha(110)
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        fmt.setFontWeight(QFont.Weight.Bold)
        block = first
        while block.isValid():
            text = block.text()
            # (A separate, uncached match: History's lines would push the live captions' out of the cache.)
            for start, end in utf16_spans(text, self.keywords.spans_uncached(text)):
                cursor.setPosition(block.position() + start)
                cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(fmt)
            block = block.next()

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
