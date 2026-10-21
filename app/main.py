"""AppLiveCaption entry point.

    python -m app.main [--start] [--minimized]

    --start       start captioning as soon as the model has loaded
    --minimized   start with the main window minimized (e.g. when launched at sign-in)
"""

from __future__ import annotations

import os
import sys

# No network at runtime: models load from local folders only. The downloader
# lifts this flag for its own requests while a download runs.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# The app doesn't use PyTorch. CTranslate2 imports it when it's installed (only
# its model converters need it), which costs over a second at start-up and
# loads PyTorch's own CUDA libraries instead of the ones the app uses.
sys.modules.setdefault("torch", None)

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_exception_hooks() -> None:
    import logging
    import threading

    log = logging.getLogger("app")

    def excepthook(exc_type, exc, tb):
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args):
        log.critical("Uncaught exception in thread %s", args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def main() -> int:
    from app.logging_setup import setup_logging

    setup_logging(console=sys.stderr is not None)
    _install_exception_hooks()
    import logging

    from app import APP_NAME, __version__

    logging.getLogger("app").info("Starting %s %s (frozen=%s)", APP_NAME, __version__, getattr(sys, "frozen", False))

    from app.asr.cuda_libs import remove_if_scheduled

    remove_if_scheduled()  # GPU support the user removed; must run before any CUDA library loads

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)

    from app.i18n import install_translator

    install_translator(app)

    from qfluentwidgets import setTheme, setThemeColor

    from app.config import cfg

    setTheme(cfg.get(cfg.themeMode))
    if sys.platform == "win32":
        # Use the Windows accent color, like built-in apps do.
        from qframelesswindow.utils import getSystemAccentColor

        accent = getSystemAccentColor()
        if accent.isValid():
            setThemeColor(accent)

    from app.ui.main_window import MainWindow

    window = MainWindow(auto_start="--start" in sys.argv)
    if "--minimized" in sys.argv:
        window.showMinimized()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
