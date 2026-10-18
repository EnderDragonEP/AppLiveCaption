"""The app icon, drawn in code so no image files are needed at runtime."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

from ..paths import resource_dir


def _draw(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(size * 0.04, size * 0.14, size * 0.92, size * 0.72)
    path = QPainterPath()
    path.addRoundedRect(r, size * 0.16, size * 0.16)
    p.fillPath(path, QColor(0, 120, 212))
    # Two caption lines.
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    h = size * 0.09
    p.drawRoundedRect(QRectF(size * 0.18, size * 0.36, size * 0.64, h), h / 2, h / 2)
    p.drawRoundedRect(QRectF(size * 0.18, size * 0.55, size * 0.42, h), h / 2, h / 2)
    p.end()
    return pm


def app_icon() -> QIcon:
    ico = resource_dir() / "assets" / "app.ico"
    if ico.is_file():
        return QIcon(str(ico))
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw(s))
    return icon


def save_ico(path: str) -> None:
    """Write the icon as .ico (used to create assets/app.ico for the build)."""
    _draw(256).save(path, "ICO")
