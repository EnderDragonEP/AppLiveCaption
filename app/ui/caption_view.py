"""Bottom-anchored caption rendering used by the Live page and the overlay."""

from __future__ import annotations

import html
import math

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


# More finished lines than any window can show; the height decides how many are drawn.
MAX_LINES = 40


def draw_captions(painter: QPainter, model: CaptionModel, rect: QRectF, font: QFont, final_color: QColor,
                  partial_color: QColor) -> bool:
    """Draw as many of the newest lines as fit in ``rect``, anchored to its bottom.

    The cut is at a whole text line, never through one, so a taller window
    simply shows more lines. Returns False when there is nothing to draw.
    """
    finals, partial = model.last_lines(MAX_LINES)
    if not finals and not partial:
        return False
    doc = build_document(finals, partial, font, final_color, partial_color, rect.width())
    height = doc.size().height()
    # The top of the oldest text line that still fits in full (or of the newest
    # line, if not even that one fits).
    visible_top = height - rect.height()
    first_top = None
    hidden_bottom = 0.0  # bottom of the line above the first one shown
    block = doc.lastBlock()
    while block.isValid():
        text_layout = block.layout()
        y0 = text_layout.position().y()
        for i in reversed(range(text_layout.lineCount())):
            line = text_layout.lineAt(i)
            top = y0 + line.y()
            if top < visible_top and first_top is not None:
                hidden_bottom = top + line.height()
                break
            first_top = top
        else:
            block = block.previous()
            continue
        break
    first_top = first_top or 0.0
    top = rect.bottom() - height  # where the document starts so its end sits at the bottom
    # Text lines overlap by a pixel and anti-aliasing reaches one more, so the
    # visible part starts a pixel below the hidden line's box, on a whole pixel
    # (there's always some empty space above the tallest glyphs). drawContents()
    # replaces the painter's clip with this rectangle.
    cut = math.ceil(top + max(first_top, hidden_bottom)) + (1 if hidden_bottom else 0)
    clip_top = max(rect.top(), cut) - top
    painter.save()
    painter.translate(rect.left(), top)
    doc.drawContents(painter, QRectF(0, clip_top, rect.width(), height - clip_top))
    painter.restore()
    return True


class CaptionView(QWidget):
    """Draws the newest caption lines anchored to the bottom, like Windows Live Captions."""

    def __init__(self, model: CaptionModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.font_ = QFont("Microsoft JhengHei UI", 24)
        self.final_color = QColor(32, 32, 32)
        self.partial_color = QColor(128, 128, 128)
        self.placeholder = ""
        self.placeholder_color = QColor(140, 140, 140)
        model.changed.connect(self.update)
        self.setMinimumHeight(120)

    def set_style(self, font: QFont, final_color: QColor, partial_color: QColor) -> None:
        self.font_ = font
        self.final_color = final_color
        self.partial_color = partial_color
        self.update()

    def set_placeholder(self, text: str) -> None:
        self.placeholder = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        margin = 20
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        # The newest lines, anchored to the bottom; as many as the page's height fits.
        if not draw_captions(painter, self.model, QRectF(rect), self.font_, self.final_color, self.partial_color) \
                and self.placeholder:
            painter.setPen(self.placeholder_color)
            f = QFont(self.font_)
            f.setPointSizeF(max(11.0, self.font_.pointSizeF() * 0.6))
            painter.setFont(f)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
