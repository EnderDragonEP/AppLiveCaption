"""Outlining a card for a moment, to show the user where to look (after "Take me there" and the like)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from qfluentwidgets import themeColor


class FlashMixin:
    """For a rounded card widget: ``flash()`` outlines it in the accent color, then fades the outline out.

    Put it before the widget class: ``class MyCard(FlashMixin, CardWidget)``.
    """

    _highlight = 0.0

    def flash(self) -> None:
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
        if self._highlight <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(themeColor())
        color.setAlphaF(self._highlight)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
