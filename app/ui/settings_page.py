"""Settings page."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QFileDialog, QFontDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBox,
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
from ..models.catalog import is_default_catalog, load_catalog
from ..models.storage import models_dir
from ..paths import log_dir, to_setting
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


class DefaultModelCard(SettingCard):
    """The model loaded at start-up, chosen from the current model list."""

    def __init__(self, parent=None):
        super().__init__(FluentIcon.ROBOT, self.tr("Default model"),
                         self.tr("Loaded when the app starts (if it is downloaded)"), parent)
        self.combo = ComboBox(self)
        self.combo.setMinimumWidth(220)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        self.combo.currentIndexChanged.connect(self._on_index)
        self.refresh_models()

    def refresh_models(self) -> None:
        """Show the current model list (it changes when models are added or removed)."""
        models = load_catalog()
        self.combo.blockSignals(True)
        self.combo.clear()
        for m in models:
            self.combo.addItem(m.name, userData=m.id)
        ids = [m.id for m in models]
        wanted = cfg.get(cfg.defaultModel)
        self.combo.setCurrentIndex(ids.index(wanted) if wanted in ids else -1)  # a removed model: shows nothing
        self.combo.blockSignals(False)
        self.combo.setEnabled(bool(models))

    def _on_index(self, index: int) -> None:
        if index >= 0:
            cfg.set(cfg.defaultModel, self.combo.itemData(index))


class SettingsPage(ScrollArea):
    deviceChanged = Signal(str)
    modelFolderChanged = Signal(str)
    resetCatalogRequested = Signal()

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
        g.addSettingCard(CustomColorSettingCard(cfg.finalColor, FluentIcon.PALETTE, self.tr("Caption color"),
                                                self.tr("Finished lines"), g))
        g.addSettingCard(CustomColorSettingCard(cfg.partialColor, FluentIcon.PALETTE, self.tr("Partial caption color"),
                                                self.tr("The line that is still being recognized"), g))
        self.layout_.addWidget(g)

        # Overlay
        g = SettingCardGroup(self.tr("Floating overlay"), container)
        g.addSettingCard(RangeSettingCard(cfg.overlayFontSize, FluentIcon.FONT_SIZE, self.tr("Overlay text size"),
                                          self.tr("Font size in the floating overlay"), g))
        g.addSettingCard(RangeSettingCard(cfg.overlayOpacity, FluentIcon.TRANSPARENT, self.tr("Background opacity"),
                                          self.tr("0% is fully see-through, 100% is solid"), g))
        g.addSettingCard(CustomColorSettingCard(cfg.overlayTextColor, FluentIcon.PALETTE, self.tr("Overlay text color"),
                                                self.tr("The line still being recognized is shown in a softer shade"),
                                                g))
        g.addSettingCard(CustomColorSettingCard(cfg.overlayBackground, FluentIcon.PALETTE,
                                                self.tr("Overlay background color"),
                                                self.tr("Its transparency is set by Background opacity"), g))
        g.addSettingCard(SwitchSettingCard(FluentIcon.PIN, self.tr("Click-through"),
                                           self.tr("Mouse clicks pass through the overlay to the window below"),
                                           cfg.overlayClickThrough, g))
        self.layout_.addWidget(g)

        # Speech recognition
        g = SettingCardGroup(self.tr("Speech recognition"), container)
        self.default_model_card = DefaultModelCard(g)
        g.addSettingCard(self.default_model_card)
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
                                           str(models_dir()), g)
        self.folder_card.clicked.connect(self._choose_folder)
        g.addSettingCard(self.folder_card)
        self.reset_card = PushSettingCard(self.tr("Reset"), FluentIcon.SYNC, self.tr("Reset model list"),
                                          self.tr("Bring back the preset models and remove the ones you added"), g)
        self.reset_card.clicked.connect(self.resetCatalogRequested)
        g.addSettingCard(self.reset_card)
        self.refresh_models()
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

    def refresh_models(self) -> None:
        """The model list changed: update the default-model choice and the Reset button."""
        self.default_model_card.refresh_models()
        self.reset_card.button.setEnabled(not is_default_catalog())

    def _choose_folder(self) -> None:
        current = str(models_dir())
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose the model folder"), current)
        if folder and os.path.normcase(os.path.abspath(folder)) != os.path.normcase(current):
            cfg.set(cfg.modelFolder, to_setting(folder))  # relative inside the app folder, so it can move
            self.folder_card.setContent(str(models_dir()))
            self.modelFolderChanged.emit(folder)
