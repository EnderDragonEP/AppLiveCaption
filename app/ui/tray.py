"""System tray icon."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSystemTrayIcon
from qfluentwidgets import Action, FluentIcon, SystemTrayMenu

from .. import APP_NAME
from ..config import cfg
from .icons import app_icon


class TrayIcon(QSystemTrayIcon):
    showWindowRequested = Signal()
    overlayToggled = Signal(bool)
    captionToggled = Signal()
    clickThroughToggled = Signal(bool)
    updateCheckRequested = Signal()
    quitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIcon(app_icon())
        self.setToolTip(APP_NAME)
        self.menu = SystemTrayMenu(parent=parent)
        self.show_action = Action(FluentIcon.HOME, self.tr("Show window"), triggered=lambda: self.showWindowRequested.emit())
        self.caption_action = Action(FluentIcon.PLAY_SOLID, self.tr("Start captions"), triggered=lambda: self.captionToggled.emit())
        self.overlay_action = Action(FluentIcon.TRANSPARENT, self.tr("Show overlay"))
        self.overlay_action.setCheckable(True)
        self.overlay_action.triggered.connect(lambda: self.overlayToggled.emit(self.overlay_action.isChecked()))
        self.click_action = Action(FluentIcon.PIN, self.tr("Overlay click-through"))
        self.click_action.setCheckable(True)
        self.click_action.setChecked(cfg.get(cfg.overlayClickThrough))
        self.click_action.triggered.connect(lambda: self.clickThroughToggled.emit(self.click_action.isChecked()))
        cfg.overlayClickThrough.valueChanged.connect(self.click_action.setChecked)
        self.update_action = Action(FluentIcon.UPDATE, self.tr("Check for updates"), triggered=lambda: self.updateCheckRequested.emit())
        self.quit_action = Action(FluentIcon.POWER_BUTTON, self.tr("Quit"), triggered=lambda: self.quitRequested.emit())
        self.menu.addActions([self.show_action, self.caption_action])
        self.menu.addSeparator()
        self.menu.addActions([self.overlay_action, self.click_action])
        self.menu.addSeparator()
        self.menu.addActions([self.update_action, self.quit_action])
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.showWindowRequested.emit()

    def set_running(self, running: bool) -> None:
        self.caption_action.setText(self.tr("Stop captions") if running else self.tr("Start captions"))
        self.caption_action.setIcon(FluentIcon.PAUSE_BOLD.icon() if running else FluentIcon.PLAY_SOLID.icon())

    def set_overlay_checked(self, on: bool) -> None:
        self.overlay_action.setChecked(on)
