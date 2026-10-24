"""Caption lines shared by the Live Captions page and the overlay."""

from __future__ import annotations

import collections
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor


class CaptionModel(QObject):
    changed = Signal()

    def __init__(self, max_lines: int = 200, parent=None):
        super().__init__(parent)
        self.finals: collections.deque[str] = collections.deque(maxlen=max_lines)
        self.partial = ""
        self.partial_stable = ""
        self.final_count = 0  # finals added so far (the deque only keeps the newest)
        # Keyword highlighting (app/ui/keyword_service.py): the (start, end) spans to mark in a line.
        self.highlighter: Callable[[str], list[tuple[int, int]]] | None = None
        self.highlight_color = QColor("#ffc83d")

    def set_partial(self, text: str, stable: str) -> None:
        self.partial = text
        self.partial_stable = stable
        self.changed.emit()

    def add_final(self, text: str) -> None:
        if text:
            self.finals.append(text)
            self.final_count += 1
        self.partial = ""
        self.partial_stable = ""
        self.changed.emit()

    def clear(self) -> None:
        self.finals.clear()
        self.partial = ""
        self.partial_stable = ""
        self.changed.emit()

    def last_lines(self, n: int) -> tuple[list[str], str]:
        """The newest ``n`` lines: (final lines, partial text). The partial counts as a line."""
        keep = n - 1 if self.partial else n
        finals = list(self.finals)[-keep:] if keep > 0 else []
        return finals, self.partial
