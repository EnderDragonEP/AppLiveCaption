"""Translation helpers.

UI text is English. Widgets call ``self.tr()``; code outside QObject classes uses :func:`tr`, and
language names go through :func:`language_label`. Translations are ``app/i18n/app_<locale>.qm`` files,
made with pyside6-lupdate, Qt Linguist and pyside6-lrelease (see the README's "Translating the UI").
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

from .paths import resource_dir

log = logging.getLogger(__name__)

# pyside6-lupdate files module-level tr("...") calls under the empty context,
# so look them up there too.
CONTEXT = ""
# Where app/asr/languages.py puts the language names.
LANGUAGES_CONTEXT = "Languages"


def tr(text: str, context: str = CONTEXT) -> str:
    return QCoreApplication.translate(context, text)


def language_label(code: str, info=None) -> str:
    """A language's name in the UI language: ``info``'s (a model's) own name for it if it has one."""
    from .asr.languages import language_name

    return tr(info.language_label(code) if info is not None else language_name(code), LANGUAGES_CONTEXT)


def windows_chinese_script(tags: list[str] | None = None) -> str | None:
    """"traditional" or "simplified": the script of the first Chinese language in Windows' language list
    (``tags``, for testing), or None without one."""
    for tag in QLocale.system().uiLanguages() if tags is None else tags:
        locale = QLocale(tag)
        if locale.language() == QLocale.Language.Chinese:
            return "traditional" if locale.script() == QLocale.Script.TraditionalChineseScript else "simplified"
    return None


def _translation_for(tag: str) -> str | None:
    """The app_<name> translation for a language tag like "zh-Hant-TW", if one exists ("" for English)."""
    locale = QLocale(tag)
    if locale.language() == QLocale.Language.English:
        return ""  # the app's own language
    if locale.language() == QLocale.Language.Chinese:
        # One translation per script: Taiwan's also for Hong Kong and Macau, Mainland's also for Singapore.
        traditional = locale.script() == QLocale.Script.TraditionalChineseScript
        locale = QLocale("zh_TW" if traditional else "zh_CN")
    name = f"app_{locale.name()}"
    return name if (resource_dir() / "i18n" / f"{name}.qm").is_file() else None


AUTO_LANGUAGE = "auto"  # Settings → Language: follow Windows


def available_languages() -> list[tuple[str, str]]:
    """(code, name) of each UI language, English first, each named in its own language (so it can be
    found whatever language the app is in)."""
    languages = [("en", "English")]
    for qm in sorted((resource_dir() / "i18n").glob("app_*.qm")):
        code = qm.stem.removeprefix("app_")
        locale = QLocale(code)
        name = locale.nativeLanguageName()
        # A translation for one country or region (pt_BR) says which, except Chinese: its name already
        # says the script (繁體中文, 简体中文), which is what matters, and several places write each one.
        if "_" in code and locale.language() != QLocale.Language.Chinese:
            name += f" ({locale.nativeTerritoryName()})"
        languages.append((code, name))
    return languages


def install_translator(app) -> QTranslator | None:
    """Load the translation for Settings → Language, together with the widget library's own translation
    (dialog buttons, menus, the color picker, ...).

    "auto" takes the first language in Windows' language list that the app has. The list is walked in
    order, and English counts as available: Qt's own QTranslator.load(QLocale) would skip English (it has
    no .qm file) and pick a language the user ranked lower.
    """
    from .config import cfg

    chosen = cfg.get(cfg.uiLanguage)
    if chosen == AUTO_LANGUAGE or chosen not in dict(available_languages()):  # (a translation that's gone)
        name = next((n for n in map(_translation_for, QLocale.system().uiLanguages()) if n is not None), "")
    else:
        name = "" if chosen == "en" else f"app_{chosen}"
    if not name:
        return None
    translator = QTranslator(app)
    if not translator.load(name, str(resource_dir() / "i18n")):
        log.warning("Couldn't load the translation %s", name)
        return None
    locale = QLocale(translator.language())
    from qfluentwidgets import FluentTranslator

    # Qt asks the translator installed last first, so the app's translation can override a few of the
    # library's strings (see LIBRARY_STRINGS in app/ui/settings_page.py).
    app.installTranslator(FluentTranslator(locale, app))
    app.installTranslator(translator)
    log.info("Loaded translation for %s", locale.name())
    return translator
