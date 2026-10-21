"""Settings page."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QFileDialog, QFontDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBoxSettingCard,
    CustomColorSettingCard,
    ExpandLayout,
    FluentIcon,
    HyperlinkLabel,
    OptionsSettingCard,
    PushButton,
    PrimaryPushSettingCard,
    PushSettingCard,
    RangeSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
    TitleLabel,
    setCustomStyleSheet,
    setTheme,
)

from .. import APP_NAME, LICENSES_URL, REPO_URL, __version__
from ..config import cfg, is_win11
from ..models.catalog import load_catalog
from ..paths import log_dir
from .icons import app_icon


class AppInfoCard(PrimaryPushSettingCard):
    """App name, then the version with "View source" and "Licenses" links, plus the update button."""

    def __init__(self, parent=None):
        super().__init__(self.tr("Check for updates"), app_icon(), APP_NAME,
                         self.tr("Version {version}").format(version=__version__) + "  ·", parent)
        self.sourceLink = HyperlinkLabel(QUrl(REPO_URL), self.tr("View source"), self)
        self.licenseLink = HyperlinkLabel(QUrl(LICENSES_URL), self.tr("Licenses"), self)
        self.separator = QLabel("·", self)
        self.separator.setObjectName("contentLabel")  # styled like the version text
        # Match the version text; the card's button rule would make the links 14 px.
        link_size = "HyperlinkLabel { font-size: 11px; }"
        for link in (self.sourceLink, self.licenseLink):
            setCustomStyleSheet(link, link_size, link_size)
        # Put the links on the version line, after the card's content text.
        row = QHBoxLayout()
        row.setSpacing(6)
        self.vBoxLayout.removeWidget(self.contentLabel)
        for w in (self.contentLabel, self.sourceLink, self.separator, self.licenseLink):
            row.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.vBoxLayout.addLayout(row)


class FontSettingCard(SettingCard):
    """Picks the caption font family with QFontDialog."""

    def __init__(self, parent=None):
        super().__init__(FluentIcon.FONT, self.tr("Caption font"), cfg.get(cfg.captionFont), parent)
        self.button = PushButton(self.tr("Choose font"), self)
        self.hBoxLayout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.button.clicked.connect(self._choose)

    def _choose(self) -> None:
        current = QFont(cfg.get(cfg.captionFont), cfg.get(cfg.captionFontSize))
        ok, font = QFontDialog.getFont(current, self.window(), self.tr("Caption font"))
        # PySide6 returns (ok, font) or (font, ok) depending on the overload.
        if isinstance(ok, QFont):
            ok, font = font, ok
        if ok:
            cfg.set(cfg.captionFont, font.family())
            self.setContent(font.family())


class SettingsPage(ScrollArea):
    deviceChanged = Signal(str)
    modelFolderChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea{background:transparent;border:none}")
        body = QWidget()
        body.setStyleSheet("background:transparent")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(36, 24, 36, 24)
        outer.addWidget(TitleLabel(self.tr("Settings"), body))
        container = QWidget(body)
        self.layout_ = ExpandLayout(container)
        self.layout_.setSpacing(24)
        outer.addWidget(container)
        outer.addStretch(1)

        # Appearance
        g = SettingCardGroup(self.tr("Appearance"), container)
        self.theme_card = OptionsSettingCard(
            cfg.themeMode, FluentIcon.BRUSH, self.tr("Theme"), self.tr("Light, dark, or follow Windows"),
            texts=[self.tr("Light"), self.tr("Dark"), self.tr("Use system setting")], parent=g)
        self.theme_card.optionChanged.connect(lambda item: setTheme(cfg.get(item), lazy=True))
        g.addSettingCard(self.theme_card)
        self.mica_card = SwitchSettingCard(FluentIcon.TRANSPARENT, self.tr("Mica effect"),
                                           self.tr("Translucent window background (Windows 11)"), cfg.micaEnabled, g)
        self.mica_card.setVisible(is_win11())
        g.addSettingCard(self.mica_card)
        self.layout_.addWidget(g)

        # Captions
        g = SettingCardGroup(self.tr("Captions"), container)
        g.addSettingCard(FontSettingCard(g))
        g.addSettingCard(RangeSettingCard(cfg.captionFontSize, FluentIcon.FONT_SIZE, self.tr("Caption size"),
                                          self.tr("Font size on the Live captions page"), g))
        g.addSettingCard(RangeSettingCard(cfg.captionLines, FluentIcon.ALIGNMENT, self.tr("Caption lines"),
                                          self.tr("How many lines the Live captions page shows"), g))
        g.addSettingCard(CustomColorSettingCard(cfg.finalColor, FluentIcon.PALETTE, self.tr("Caption color"),
                                                self.tr("Finished lines"), g))
        g.addSettingCard(CustomColorSettingCard(cfg.partialColor, FluentIcon.PALETTE, self.tr("Partial caption color"),
                                                self.tr("The line that is still being recognized"), g))
        self.layout_.addWidget(g)

        # Overlay
        g = SettingCardGroup(self.tr("Floating overlay"), container)
        g.addSettingCard(RangeSettingCard(cfg.overlayLines, FluentIcon.ALIGNMENT, self.tr("Overlay lines"),
                                          self.tr("Number of caption lines in the overlay (1–3)"), g))
        g.addSettingCard(RangeSettingCard(cfg.overlayFontSize, FluentIcon.FONT_SIZE, self.tr("Overlay text size"),
                                          None, g))
        g.addSettingCard(RangeSettingCard(cfg.overlayOpacity, FluentIcon.TRANSPARENT, self.tr("Background opacity"),
                                          self.tr("Percent"), g))
        g.addSettingCard(CustomColorSettingCard(cfg.overlayTextColor, FluentIcon.PALETTE, self.tr("Overlay text color"),
                                                None, g))
        g.addSettingCard(CustomColorSettingCard(cfg.overlayBackground, FluentIcon.PALETTE,
                                                self.tr("Overlay background color"), None, g))
        g.addSettingCard(SwitchSettingCard(FluentIcon.PIN, self.tr("Click-through"),
                                           self.tr("Mouse clicks pass through the overlay to the window below"),
                                           cfg.overlayClickThrough, g))
        self.layout_.addWidget(g)

        # Speech recognition
        g = SettingCardGroup(self.tr("Speech recognition"), container)
        models = load_catalog()
        g.addSettingCard(ComboBoxSettingCard(cfg.defaultModel, FluentIcon.ROBOT, self.tr("Default model"),
                                             self.tr("Loaded when the app starts (if it is downloaded)"),
                                             texts=[m.name for m in models], parent=g))
        self.device_card = ComboBoxSettingCard(
            cfg.device, FluentIcon.SPEED_HIGH, self.tr("Device"),
            self.tr("NVIDIA GPU (CUDA, float16) is much faster; the CPU uses int8"),
            texts=[self.tr("Automatic (GPU if available)"), self.tr("NVIDIA GPU"), self.tr("CPU")], parent=g)
        cfg.device.valueChanged.connect(lambda v: self.deviceChanged.emit(str(v)))
        g.addSettingCard(self.device_card)
        g.addSettingCard(SwitchSettingCard(FluentIcon.LANGUAGE, self.tr("Traditional Chinese output"),
                                           self.tr("Convert Simplified Chinese to Traditional Chinese (OpenCC s2twp)"),
                                           cfg.opencc, g))
        self.folder_card = PushSettingCard(self.tr("Choose folder"), FluentIcon.FOLDER, self.tr("Model folder"),
                                           cfg.get(cfg.modelFolder), g)
        self.folder_card.clicked.connect(self._choose_folder)
        g.addSettingCard(self.folder_card)
        self.layout_.addWidget(g)

        # Advanced
        g = SettingCardGroup(self.tr("Advanced"), container)
        logs = PushSettingCard(self.tr("Open folder"), FluentIcon.DOCUMENT, self.tr("Logs"), str(log_dir()), g)
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir()))))
        g.addSettingCard(logs)
        self.layout_.addWidget(g)

        # About
        g = SettingCardGroup(self.tr("About"), container)
        self.about_card = AppInfoCard(g)
        g.addSettingCard(self.about_card)
        self.layout_.addWidget(g)

        self.setWidget(body)

    def _choose_folder(self) -> None:
        current = cfg.get(cfg.modelFolder)
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose the model folder"), current)
        if folder and os.path.normcase(folder) != os.path.normcase(current):
            cfg.set(cfg.modelFolder, folder)
            self.folder_card.setContent(folder)
            self.modelFolderChanged.emit(folder)
