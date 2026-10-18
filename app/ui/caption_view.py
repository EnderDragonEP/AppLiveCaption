"""Bottom-anchored caption rendering used by the Live page and the overlay."""

from __future__ import annotations

import html

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextDocument, QTextOption
from PySide6.QtWidgets import QWidget

from .caption_model import CaptionModel


def build_document(finals: list[str], partial: str, font: QFont, final_color: QColor, partial_color: QColor,
                   width: float, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> QTextDocument:
    doc = QTextDocument()
    doc.setDefaultFont(font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    opt.setAlignment(align)
    doc.setDefaultTextOption(opt)
    doc.setDocumentMargin(0)
    gap = max(2, font.pointSize() // 3)
    parts = [f'<p style="margin:0 0 {gap}px 0; color:{final_color.name()};">{html.escape(t)}</p>' for t in finals]
    if partial:
        parts.append(f'<p style="margin:0; color:{partial_color.name()};">{html.escape(partial)}</p>')
    doc.setHtml("".join(parts))
    doc.setTextWidth(width)
    return doc


class CaptionView(QWidget):
    """Draws the newest caption lines anchored to the bottom, like Windows Live Captions."""

    def __init__(self, model: CaptionModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.lines = 8
        self.font_ = QFont("Microsoft JhengHei UI", 24)
        self.final_color = QColor(32, 32, 32)
        self.partial_color = QColor(128, 128, 128)
        self.placeholder = ""
        self.placeholder_color = QColor(140, 140, 140)
        model.changed.connect(self.update)
        self.setMinimumHeight(120)

    def set_style(self, font: QFont, final_color: QColor, partial_color: QColor, lines: int) -> None:
        self.font_ = font
        self.final_color = final_color
        self.partial_color = partial_color
        self.lines = lines
        self.update()

    def set_placeholder(self, text: str) -> None:
        self.placeholder = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        margin = 20
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        finals, partial = self.model.last_lines(self.lines)
        if not finals and not partial:
            if self.placeholder:
                painter.setPen(self.placeholder_color)
                f = QFont(self.font_)
                f.setPointSizeF(max(11.0, self.font_.pointSizeF() * 0.6))
                painter.setFont(f)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            return
        doc = build_document(finals, partial, self.font_, self.final_color, self.partial_color, rect.width())
        # Anchor the last line to the bottom; older lines scroll off the top.
        top = rect.bottom() - doc.size().height()
        painter.save()
        painter.setClipRect(rect)
        painter.translate(rect.left(), top)
        doc.drawContents(painter, QRectF(0, rect.top() - top, rect.width(), rect.height()))
        painter.restore()
