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
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
# transformers copies weights on 4 threads that hold the GIL in turn, which can
# stall the UI while Breeze loads; loading them one by one is as fast here.
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

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

    logging.getLogger("app").info("Starting AppLiveCaption (frozen=%s)", getattr(sys, "frozen", False))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("AppLiveCaption")
    app.setOrganizationName("AppLiveCaption")

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
