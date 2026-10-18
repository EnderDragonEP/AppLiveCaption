"""Translation helpers.

UI text is English. Widgets call ``self.tr()``; code outside QObject classes
uses :func:`tr`. A Traditional Chinese translation can be added later by
running ``pyside6-lupdate`` over the ``app`` package, translating the .ts file
in Qt Linguist and placing the compiled ``app_zh_TW.qm`` in ``app/i18n/``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication, QLocale, QTranslator

from .paths import resource_dir

log = logging.getLogger(__name__)

# pyside6-lupdate files module-level tr("...") calls under the empty context,
# so look them up there too.
CONTEXT = ""


def tr(text: str, context: str = CONTEXT) -> str:
    return QCoreApplication.translate(context, text)


def install_translator(app) -> QTranslator | None:
    """Load app/i18n/app_<locale>.qm for the system locale, if one exists."""
    translator = QTranslator(app)
    folder = resource_dir() / "i18n"
    if translator.load(QLocale.system(), "app", "_", str(folder)):
        app.installTranslator(translator)
        log.info("Loaded translation for %s", QLocale.system().name())
        return translator
    return None
