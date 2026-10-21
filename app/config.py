"""Application settings (qfluentwidgets QConfig), saved to config.json next to the exe (app/paths.py)."""

from __future__ import annotations

import sys

from PySide6.QtGui import QColor
from qfluentwidgets import (
    BoolValidator,
    ColorConfigItem,
    ConfigItem,
    ConfigValidator,
    EnumSerializer,
    OptionsConfigItem,
    OptionsValidator,
    QConfig,
    RangeConfigItem,
    RangeValidator,
    Theme,
    qconfig,
)

from .models.catalog import load_catalog
from .paths import config_file

DEFAULT_MODEL = "whisper-large-v3-turbo"


def is_win11() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


# Relative to the app's data folder (app/paths.py), so it survives moving the app.
DEFAULT_MODEL_FOLDER = "models"


class FolderPathValidator(ConfigValidator):
    """Accepts any non-empty path; the folder is created when it is used."""

    def validate(self, value):
        return isinstance(value, str) and bool(value.strip())

    def correct(self, value):
        return value if self.validate(value) else DEFAULT_MODEL_FOLDER


class DictValidator(ConfigValidator):
    def validate(self, value):
        return isinstance(value, dict)

    def correct(self, value):
        return value if isinstance(value, dict) else {}


class Config(QConfig):
    # Follow the system theme by default (QConfig's own default is light).
    themeMode = OptionsConfigItem("QFluentWidgets", "ThemeMode", Theme.AUTO, OptionsValidator(Theme), EnumSerializer(Theme))
    micaEnabled = ConfigItem("Appearance", "MicaEnabled", is_win11(), BoolValidator())

    # Live captions
    modelId = ConfigItem("Live", "Model", DEFAULT_MODEL)
    language = ConfigItem("Live", "Language", "auto")
    captureSelection = ConfigItem("Live", "CaptureSelection", {}, DictValidator())

    # Speech recognition
    defaultModel = OptionsConfigItem("ASR", "DefaultModel", DEFAULT_MODEL,
                                     OptionsValidator([m.id for m in load_catalog()]))
    device = OptionsConfigItem("ASR", "Device", "auto", OptionsValidator(["auto", "cuda", "cpu"]))
    # Suggest downloading GPU support at start-up when an NVIDIA GPU has no CUDA libraries.
    offerGpuSupport = ConfigItem("ASR", "OfferGpuSupport", True, BoolValidator())
    opencc = ConfigItem("ASR", "OpenCC", True, BoolValidator())
    modelFolder = ConfigItem("ASR", "ModelFolder", DEFAULT_MODEL_FOLDER, FolderPathValidator())

    # Caption appearance (Live Captions page)
    captionFont = ConfigItem("Captions", "FontFamily", "Microsoft JhengHei UI")
    captionFontSize = RangeConfigItem("Captions", "FontSize", 24, RangeValidator(10, 72))
    captionLines = RangeConfigItem("Captions", "Lines", 8, RangeValidator(1, 30))
    # A caption color left at its default follows the light/dark theme instead.
    finalColor = ColorConfigItem("Captions", "FinalColor", QColor(32, 32, 32))
    partialColor = ColorConfigItem("Captions", "PartialColor", QColor(128, 128, 128))

    # Floating overlay
    overlayVisible = ConfigItem("Overlay", "Visible", False, BoolValidator())
    overlayGeometry = ConfigItem("Overlay", "Geometry", [])
    overlayClickThrough = ConfigItem("Overlay", "ClickThrough", False, BoolValidator())
    overlayLines = RangeConfigItem("Overlay", "Lines", 2, RangeValidator(1, 3))
    overlayFontSize = RangeConfigItem("Overlay", "FontSize", 26, RangeValidator(10, 72))
    overlayOpacity = RangeConfigItem("Overlay", "BackgroundOpacity", 70, RangeValidator(0, 100))
    overlayTextColor = ColorConfigItem("Overlay", "TextColor", QColor(255, 255, 255))
    overlayBackground = ColorConfigItem("Overlay", "BackgroundColor", QColor(0, 0, 0))


def _migrate_custom_colors_switch() -> None:
    """Settings used to have a "Custom caption colors" switch. Each color card now
    has its own Default/Custom choice, so with the switch off the colors go back
    to their defaults (which follow the theme), as they looked before. Saving
    drops the old key, so this runs once."""
    cfg.set(cfg.finalColor, cfg.finalColor.defaultValue)
    cfg.set(cfg.partialColor, cfg.partialColor.defaultValue)


cfg = Config()
_legacy_switch_off = False
try:
    import json as _json

    _legacy_switch_off = _json.loads(config_file().read_text(encoding="utf-8")).get("Captions", {}).get("CustomColors") is False
except (OSError, ValueError):
    pass
qconfig.load(str(config_file()), cfg)
if _legacy_switch_off:
    _migrate_custom_colors_switch()
