"""Double-click launcher: runs AppLiveCaption from source, without a build.

Windows opens .pyw files with the Python the py launcher picks (normally the
newest installed one), and the app uses that Python's packages. Arguments are
passed through:

    AppLiveCaption.pyw --start --minimized
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Import name -> pip package (as in requirements.txt), for the packages the app can't start without.
REQUIRED = {
    "PySide6": "PySide6",
    "qfluentwidgets": "PySide6-Fluent-Widgets",
    "faster_whisper": "faster-whisper",
    "ctranslate2": "ctranslate2",
    "onnxruntime": "onnxruntime",
    "pyaudiowpatch": "PyAudioWPatch",
    "comtypes": "comtypes",
    "pycaw": "pycaw",
    "psutil": "psutil",
    "soxr": "soxr",
    "opencc": "opencc-python-reimplemented",
    "numpy": "numpy",
    "huggingface_hub": "huggingface_hub",
}


def _show_error(text: str) -> None:
    # pythonw has no console, so errors go to a message box.
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, "AppLiveCaption", 0x10)  # MB_ICONERROR


def _console_python() -> str:
    exe = sys.executable
    folder, name = os.path.split(exe)
    return os.path.join(folder, "python.exe") if name.lower() == "pythonw.exe" else exe


def _check_environment() -> str | None:
    """Return a message describing what's missing, or None if the app can start."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if sys.version_info < (3, 11):
        return (f"AppLiveCaption needs Python 3.11 or newer, but it was opened with Python {version}:\n"
                f"    {sys.executable}")
    missing = [pkg for mod, pkg in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        return None
    return (
        f"This Python is missing packages that AppLiveCaption needs:\n"
        f"    {', '.join(missing)}\n\n"
        f"Python {version}: {sys.executable}\n\n"
        f"Install them by running this in a terminal:\n"
        f"    \"{_console_python()}\" -m pip install -r \"{os.path.join(ROOT, 'requirements.txt')}\""
    )


if __name__ == "__main__":
    problem = _check_environment()
    if problem:
        _show_error(problem)
        sys.exit(1)

    sys.path.insert(0, ROOT)
    from app.main import main

    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - nothing else would show it under pythonw
        from app.paths import log_dir

        _show_error(f"AppLiveCaption stopped because of an error:\n{exc}\n\nDetails are in {log_dir() / 'app.log'}")
        raise
