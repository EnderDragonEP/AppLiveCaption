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


def _translation_for(tag: str) -> str | None:
    """The app_<name> translation for a language tag like "zh-Hant-TW", if one exists ("" for English)."""
    locale = QLocale(tag)
    if locale.language() == QLocale.Language.English:
        return ""  # the app's own language
    if locale.language() == QLocale.Language.Chinese and locale.script() == QLocale.Script.TraditionalChineseScript:
        locale = QLocale("zh_TW")  # also for Hong Kong and Macau: the Taiwan translation reads fine there
    name = f"app_{locale.name()}"
    return name if (resource_dir() / "i18n" / f"{name}.qm").is_file() else None


def install_translator(app) -> QTranslator | None:
    """Load the translation for the first language in Windows' language list that the app has, together
    with the widget library's own translation (dialog buttons, menus, the color picker, ...).

    The list is walked in order, and English counts as available: Qt's own QTranslator.load(QLocale)
    would skip English (it has no .qm file) and pick a language the user ranked lower.
    """
    name = next((n for n in map(_translation_for, QLocale.system().uiLanguages()) if n is not None), "")
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
