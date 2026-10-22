"""Floating caption overlay: frameless, always on top, semi-transparent."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon, Theme, ToolTipFilter, TransparentToolButton

from ..config import cfg
from .caption_model import CaptionModel
from .caption_view import draw_captions

EDGE = 8  # resize margin in pixels


class CaptionOverlay(QWidget):
    closed = Signal()

    def __init__(self, model: CaptionModel):
        super().__init__(None)
        self.model = model
        self.setWindowTitle("Captions")
        self._apply_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(320, 70))
        self._drag_offset: QPoint | None = None
        self._resize_edges: Qt.Edges | None = None
        self._press_geom = QRect()
        self._press_pos = QPoint()
        self._hover = False

        self.lock_btn = TransparentToolButton(FluentIcon.PIN, self)
        self.lock_btn.setToolTip(self.tr("Click-through (turn off from the tray icon)"))
        self.lock_btn.installEventFilter(ToolTipFilter(self.lock_btn))
        self.lock_btn.clicked.connect(lambda: cfg.set(cfg.overlayClickThrough, True))
        self.close_btn = TransparentToolButton(FluentIcon.CLOSE, self)
        self.close_btn.setToolTip(self.tr("Hide overlay"))
        self.close_btn.installEventFilter(ToolTipFilter(self.close_btn))
        self.close_btn.clicked.connect(self._hide_clicked)
        for b in (self.lock_btn, self.close_btn):
            b.setFixedSize(28, 28)
            b.hide()
        self._update_button_icons()
        cfg.overlayBackground.valueChanged.connect(lambda _v: self._update_button_icons())

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self.save_geometry)

        model.changed.connect(self.update)
        for item in (cfg.overlayFontSize, cfg.overlayOpacity, cfg.overlayTextColor,
                     cfg.overlayBackground, cfg.captionFont):
            item.valueChanged.connect(lambda _v: self.update())
        cfg.overlayClickThrough.valueChanged.connect(self._on_click_through)
        self._restore_geometry()

    # -- window flags -------------------------------------------------------
    def _apply_flags(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if cfg.get(cfg.overlayClickThrough):
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

    def _on_click_through(self, _on: bool) -> None:
        visible = self.isVisible()
        self._apply_flags()  # changing flags hides the window
        self._set_buttons_visible(False)
        if visible:
            self.show()

    # -- geometry --------------------------------------------------------------
    def _restore_geometry(self) -> None:
        geo = cfg.get(cfg.overlayGeometry)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(geo, list) and len(geo) == 4:
            rect = QRect(*[int(v) for v in geo])
            # Only restore if it is still on a connected screen.
            if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
                self.setGeometry(rect)
                return
        w, h = min(900, int(screen.width() * 0.6)), 120
        self.setGeometry(screen.x() + (screen.width() - w) // 2, screen.bottom() - h - 60, w, h)

    def save_geometry(self) -> None:
        g = self.geometry()
        cfg.set(cfg.overlayGeometry, [g.x(), g.y(), g.width(), g.height()])

    def moveEvent(self, e):  # noqa: N802
        self._save_timer.start()
        super().moveEvent(e)

    def resizeEvent(self, e):  # noqa: N802
        self.close_btn.move(self.width() - 34, 6)
        self.lock_btn.move(self.width() - 64, 6)
        self._save_timer.start()
        super().resizeEvent(e)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(cfg.get(cfg.overlayBackground))
        alpha = int(cfg.get(cfg.overlayOpacity) * 2.55)
        if self._hover:
            alpha = max(alpha, 90)
        bg.setAlpha(alpha)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.fillPath(path, bg)
        if self._hover and not cfg.get(cfg.overlayClickThrough):
            p.setPen(QColor(255, 255, 255, 70))
            p.drawPath(path)

        margin = 16
        rect = self.rect().adjusted(margin, 10, -margin, -10)
        text_color = QColor(cfg.get(cfg.overlayTextColor))
        partial_color = QColor(text_color)
        partial_color.setAlpha(255)
        partial_color = partial_color.darker(135) if text_color.lightness() > 128 else partial_color.lighter(160)
        font = QFont(cfg.get(cfg.captionFont), cfg.get(cfg.overlayFontSize))
        font.setWeight(QFont.Weight.DemiBold)
        # As many lines as the overlay's height fits: resize it to show more or fewer.
        draw_captions(p, self.model, QRectF(rect), font, text_color, partial_color)

    # -- interaction -------------------------------------------------------------
    def _edges_at(self, pos: QPoint) -> Qt.Edges:
        edges = Qt.Edges()
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edges):
        left_right = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        top_bottom = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if left_right and top_bottom:
            tl_br = (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or (
                edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge)
            return Qt.CursorShape.SizeFDiagCursor if tl_br else Qt.CursorShape.SizeBDiagCursor
        if left_right:
            return Qt.CursorShape.SizeHorCursor
        if top_bottom:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeAllCursor

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position().toPoint()
        edges = self._edges_at(pos)
        handle = self.windowHandle()
        # Let the window manager move/resize the window when it can (smooth, snaps to edges).
        if handle is not None:
            if edges and handle.startSystemResize(edges):
                return
            if not edges and handle.startSystemMove():
                return
        self._resize_edges = edges or None
        self._press_geom = self.geometry()
        self._press_pos = e.globalPosition().toPoint()
        self._drag_offset = None if edges else pos

    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        if e.buttons() & Qt.MouseButton.LeftButton:
            gp = e.globalPosition().toPoint()
            if self._drag_offset is not None:
                self.move(gp - self._drag_offset)
            elif self._resize_edges:
                d = gp - self._press_pos
                g = QRect(self._press_geom)
                if self._resize_edges & Qt.Edge.LeftEdge:
                    g.setLeft(min(g.left() + d.x(), g.right() - self.minimumWidth()))
                if self._resize_edges & Qt.Edge.RightEdge:
                    g.setRight(g.right() + d.x())
                if self._resize_edges & Qt.Edge.TopEdge:
                    g.setTop(min(g.top() + d.y(), g.bottom() - self.minimumHeight()))
                if self._resize_edges & Qt.Edge.BottomEdge:
                    g.setBottom(g.bottom() + d.y())
                self.setGeometry(g)
            return
        self.setCursor(QCursor(self._cursor_for(self._edges_at(pos))))

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag_offset = None
        self._resize_edges = None
        self.save_geometry()

    def enterEvent(self, e):  # noqa: N802
        self._hover = True
        self._set_buttons_visible(True)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self._hover = False
        self._set_buttons_visible(False)
        self.update()
        super().leaveEvent(e)

    def _update_button_icons(self) -> None:
        # Light icons on a dark background and vice versa, whatever the app theme.
        theme = Theme.DARK if QColor(cfg.get(cfg.overlayBackground)).lightness() < 128 else Theme.LIGHT
        self.lock_btn.setIcon(FluentIcon.PIN.icon(theme))
        self.close_btn.setIcon(FluentIcon.CLOSE.icon(theme))

    def _set_buttons_visible(self, on: bool) -> None:
        on = on and not cfg.get(cfg.overlayClickThrough)
        self.lock_btn.setVisible(on)
        self.close_btn.setVisible(on)

    def _hide_clicked(self) -> None:
        self.hide()
        self.closed.emit()
