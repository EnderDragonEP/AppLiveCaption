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

from .asr.textconv import CHINESE_OFF, CHINESE_OUTPUTS, CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL
from .core.keywords import FUZZY
from .paths import config_file

# Caption fonts drawn the way Traditional and Simplified Chinese readers expect.
TRADITIONAL_FONT, SIMPLIFIED_FONT = "Microsoft JhengHei UI", "Microsoft YaHei UI"

DEFAULT_MODEL = "whisper-large-v3-turbo"
# Settings → Default model choices besides a model id (model ids never contain ":").
NO_MODEL = ":none"  # load nothing at start-up
PREVIOUS_MODEL = ":previous"  # load the model used last time


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


class KeywordListValidator(ConfigValidator):
    """[{"text": str, "enabled": bool, "sound": bool}, ...]: the Keywords page's list. "sound" is the
    keyword's "Match by sound" (for plain Latin letters that spell Pinyin); it may be missing (off)."""

    def validate(self, value):
        return isinstance(value, list) and all(
            isinstance(k, dict) and isinstance(k.get("text"), str) and isinstance(k.get("enabled", True), bool)
            and isinstance(k.get("sound", False), bool) for k in value)

    def correct(self, value):
        if not isinstance(value, list):
            return []
        return [{"text": k["text"], "enabled": bool(k.get("enabled", True)), "sound": bool(k.get("sound", False))}
                for k in value if isinstance(k, dict) and isinstance(k.get("text"), str) and k["text"].strip()]


class FuzzyValidator(ConfigValidator):
    """The 模糊音 pairs switched on (app/core/keywords.py FUZZY)."""

    def validate(self, value):
        return isinstance(value, list) and all(v in FUZZY for v in value)

    def correct(self, value):
        return [v for v in value if v in FUZZY] if isinstance(value, list) else []


class Config(QConfig):
    # Follow the system theme by default (QConfig's own default is light).
    themeMode = OptionsConfigItem("QFluentWidgets", "ThemeMode", Theme.AUTO, OptionsValidator(Theme), EnumSerializer(Theme))
    micaEnabled = ConfigItem("Appearance", "MicaEnabled", is_win11(), BoolValidator())
    # The UI language: "auto" (Windows' language list), "en", or a translation's locale such as "zh_TW"
    # (app/i18n.py). It applies when the app starts.
    uiLanguage = ConfigItem("Appearance", "Language", "auto")
    # First-time setup (app/ui/setup_dialog.py) was shown and finished or skipped. Settings can open it again.
    setupDone = ConfigItem("General", "SetupDone", False, BoolValidator())
    # Minimizing hides the window; the tray icon brings it back. The first time, a tray message says so.
    minimizeToTray = ConfigItem("General", "MinimizeToTray", True, BoolValidator())
    trayHintShown = ConfigItem("General", "TrayHintShown", False, BoolValidator())

    # Live captions
    modelId = ConfigItem("Live", "Model", DEFAULT_MODEL)
    language = ConfigItem("Live", "Language", "auto")
    captureSelection = ConfigItem("Live", "CaptureSelection", {}, DictValidator())

    # Speech recognition
    # The model loaded at start-up: NO_MODEL, PREVIOUS_MODEL or any model id (the list of models can
    # change, so the id may be one the user added, or removed).
    defaultModel = ConfigItem("ASR", "DefaultModel", NO_MODEL)
    device = OptionsConfigItem("ASR", "Device", "auto", OptionsValidator(["auto", "cuda", "cpu"]))
    # Convert Chinese captions to Traditional or Simplified Chinese, or not (app/asr/textconv.py).
    # The first run picks it from Windows' language list (_first_run_defaults).
    chineseOutput = OptionsConfigItem("ASR", "ChineseOutput", CHINESE_TRADITIONAL, OptionsValidator(CHINESE_OUTPUTS))
    modelFolder = ConfigItem("ASR", "ModelFolder", DEFAULT_MODEL_FOLDER, FolderPathValidator())

    # Caption appearance (Live Captions page)
    captionFont = ConfigItem("Captions", "FontFamily", TRADITIONAL_FONT)  # (Simplified: see _first_run_defaults)
    captionFontSize = RangeConfigItem("Captions", "FontSize", 24, RangeValidator(10, 72))
    # A caption color left at its default follows the light/dark theme instead.
    finalColor = ColorConfigItem("Captions", "FinalColor", QColor(32, 32, 32))
    partialColor = ColorConfigItem("Captions", "PartialColor", QColor(128, 128, 128))

    # Floating overlay
    overlayVisible = ConfigItem("Overlay", "Visible", False, BoolValidator())
    overlayGeometry = ConfigItem("Overlay", "Geometry", [])
    overlayClickThrough = ConfigItem("Overlay", "ClickThrough", False, BoolValidator())
    overlayFontSize = RangeConfigItem("Overlay", "FontSize", 26, RangeValidator(10, 72))
    overlayOpacity = RangeConfigItem("Overlay", "BackgroundOpacity", 70, RangeValidator(0, 100))
    overlayTextColor = ColorConfigItem("Overlay", "TextColor", QColor(255, 255, 255))
    overlayBackground = ColorConfigItem("Overlay", "BackgroundColor", QColor(0, 0, 0))

    # Keyword detection (the Keywords page, app/ui/keyword_service.py)
    keywords = ConfigItem("Keywords", "List", [], KeywordListValidator())
    keywordHighlight = ConfigItem("Keywords", "Highlight", True, BoolValidator())
    keywordColor = ColorConfigItem("Keywords", "HighlightColor", QColor("#ffc83d"))
    keywordSound = ConfigItem("Keywords", "Sound", True, BoolValidator())
    keywordNotify = ConfigItem("Keywords", "Notify", True, BoolValidator())
    keywordFuzzy = ConfigItem("Keywords", "Fuzzy", [], FuzzyValidator())


def _migrate_custom_colors_switch() -> None:
    """Settings used to have a "Custom caption colors" switch. Each color card now
    has its own Default/Custom choice, so with the switch off the colors go back
    to their defaults (which follow the theme), as they looked before. Saving
    drops the old key, so this runs once."""
    cfg.set(cfg.finalColor, cfg.finalColor.defaultValue)
    cfg.set(cfg.partialColor, cfg.partialColor.defaultValue)


def _first_run_defaults() -> None:
    """No settings saved yet: match the Chinese output and the caption font to Windows' language list,
    when it has Simplified Chinese before any Traditional (the defaults suit Traditional and the rest)."""
    from .i18n import windows_chinese_script

    if windows_chinese_script() == "simplified":
        cfg.set(cfg.chineseOutput, CHINESE_SIMPLIFIED)
        cfg.set(cfg.captionFont, SIMPLIFIED_FONT)


cfg = Config()
try:
    import json as _json

    _saved = _json.loads(config_file().read_text(encoding="utf-8"))
except FileNotFoundError:
    _saved = None  # the first run
except (OSError, ValueError):
    _saved = {}
if _saved is not None and not isinstance(_saved, dict):
    _saved = {}
qconfig.load(str(config_file()), cfg)
if _saved is None:
    _first_run_defaults()
else:
    if _saved.get("Captions", {}).get("CustomColors") is False:
        _migrate_custom_colors_switch()
    # "Traditional Chinese output" used to be a switch.
    _asr = _saved.get("ASR", {})
    if "OpenCC" in _asr and "ChineseOutput" not in _asr:
        cfg.set(cfg.chineseOutput, CHINESE_TRADITIONAL if _asr["OpenCC"] else CHINESE_OFF)
