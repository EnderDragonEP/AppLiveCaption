# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for AppLiveCaption.

    pyinstaller AppLiveCaption.spec --noconfirm

Output: dist/AppLiveCaption/AppLiveCaption.exe. Models and NVIDIA's CUDA
libraries are not bundled; the app downloads them when the user asks, into
the models and cuda folders next to the exe (the app is portable; app/paths.py).

Environment switch:
    ALC_CONSOLE=1         keep a console window for debugging.
"""

import glob
import os
import sys
from pathlib import Path, PurePath

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = os.path.abspath(SPECPATH)  # noqa: F821 - defined by PyInstaller
sys.path.insert(0, os.path.join(ROOT, "tools"))
from build_excludes import unused_modules  # noqa: E402

# Name, version and author come from app/__init__.py, the single place they're defined.
_meta: dict = {}
exec(Path(ROOT, "app", "__init__.py").read_text(encoding="utf-8"), _meta)
APP_NAME, APP_VERSION = _meta["APP_NAME"], _meta["__version__"]
AUTHOR, DESCRIPTION = _meta["__author__"], _meta["DESCRIPTION"]
_numeric = tuple(int(p) for p in APP_VERSION.split(".")[:4])
_numeric += (0,) * (4 - len(_numeric))
# Shown in the .exe's Properties > Details.
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_numeric, prodvers=_numeric),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", APP_NAME),
            StringStruct("FileDescription", APP_NAME),
            StringStruct("FileVersion", APP_VERSION),
            StringStruct("ProductVersion", APP_VERSION),
            StringStruct("InternalName", APP_NAME),
            StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
            StringStruct("CompanyName", AUTHOR),  # shown as "Publisher" in Task Manager's Startup apps
            StringStruct("LegalCopyright", f"Copyright (C) 2026 {AUTHOR}. Licensed under GPL-3.0."),
            StringStruct("Comments", DESCRIPTION),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)
CONSOLE = os.environ.get("ALC_CONSOLE", "0") == "1"

datas = [
    (os.path.join(ROOT, "app", "models", "catalog.json"), "app/models"),
    (os.path.join(ROOT, "app", "assets"), "app/assets"),
]
if glob.glob(os.path.join(ROOT, "app", "i18n", "*.qm")):
    datas.append((os.path.join(ROOT, "app", "i18n", "*.qm"), "app/i18n"))
datas += collect_data_files("faster_whisper")  # bundled Silero VAD model
datas += collect_data_files("opencc")  # s2twp profile and dictionaries
datas += copy_metadata("faster-whisper") + copy_metadata("huggingface_hub") + copy_metadata("tokenizers")

# ctranslate2.dll, cudnn64_9.dll (cuDNN's loader), libiomp5md.dll. The rest of
# CUDA (cuBLAS, cuDNN, 1.3 GB) is downloaded by the app (app/asr/cuda_libs.py).
binaries = collect_dynamic_libs("ctranslate2")

hiddenimports = collect_submodules("app") + [
    "pyaudiowpatch",
    "pycaw.pycaw",
    "pycaw.api.audiopolicy",
    "comtypes.stream",
    "soxr",
    "opencc",
    "onnxruntime",
    # NumPy 2.3 no longer imports this module where PyInstaller sees it. Newer
    # hooks add it, but PyInstaller 6.12's doesn't, and the frozen app then fails
    # with "CPU dispatcher tracer already initialized".
    "numpy._core._exceptions",
]

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "ruff",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    # qfluentwidgets' [full] extras, only used by its blurred AcrylicLabel (the app has none;
    # the widget falls back to a plain image without them)
    "scipy", "PIL", "colorthief",
    # pywin32's MFC UI and COM code generator (pulled in via win32com, never used)
    "win32ui", "pywin", "win32com.client.makepy",
    # PyAV and FFmpeg (84 MB): faster-whisper only uses them to read audio files;
    # tools/pyi_rth_no_av.py stands in for the import.
    "av",
    # Hugging Face's Xet client (8 MB): the model downloader has its own HTTP
    # transfer loop and never uses it.
    "hf_xet",
]

# Qt files a widgets-only English app never loads (as in HorizontalScroll's spec):
# the software OpenGL renderer, Qt's own translations, unused image formats and
# plugins. The on-screen keyboard plugin alone pulls in Qt Quick, QML and OpenGL.
# The update check uses HTTPS through Windows' TLS (qschannelbackend), so Qt's
# OpenSSL backends can go; model downloads use Python's own ssl module
# (libssl-3.dll). The QtNetwork hook also adds any OpenSSL for Qt it finds on
# PATH (e.g. Git's libssl-3-x64.dll, 6 MB), which only that backend would load.
DROP_FILES = {
    "opengl32sw.dll", "d3dcompiler_47.dll", "qt6pdf.dll", "qopensslbackend.dll", "qcertonlybackend.dll",
    "qt6quick.dll", "qt6qml.dll", "qt6qmlmodels.dll", "qt6qmlmeta.dll", "qt6qmlworkerscript.dll",
    "qt6opengl.dll", "qt6virtualkeyboard.dll", "libssl-3-x64.dll", "libcrypto-3-x64.dll",
}
DROP_PLUGIN_DIRS = {"networkinformation", "platforminputcontexts", "generic"}
DROP_IMAGE_PLUGINS = {"qgif", "qjpeg", "qtiff", "qwebp", "qicns", "qtga", "qwbmp", "qpdf"}


# NVIDIA's CUDA libraries (except CTranslate2's cuDNN loader), in case a hook
# collects them from an installed nvidia-* package or the CUDA Toolkit.
CUDA_PREFIXES = ("cublas", "cudnn_", "nvrtc", "nvblas", "cudart", "cufft", "curand", "cusparse", "cusolver")


def keep(entry) -> bool:
    dest = PurePath(entry[0])
    if dest.name.lower() in DROP_FILES or "translations" in dest.parts:
        return False
    if dest.name.lower().startswith(CUDA_PREFIXES) or "nvidia" in dest.parts:
        return False
    if dest.parent.name in DROP_PLUGIN_DIRS:
        return False
    return not (dest.parent.name == "imageformats" and dest.stem.lower() in DROP_IMAGE_PLUGINS)


# Leave out installed packages the app doesn't depend on. Built with a Python
# shared with other projects, PyInstaller would otherwise follow optional
# imports into them (CTranslate2 -> torch, huggingface_hub -> ...).
_unused = unused_modules(Path(ROOT) / "requirements.txt")
print(f"AppLiveCaption.spec: excluding {len(_unused)} installed modules the app doesn't use")
excludes += _unused

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "app", "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[os.path.join(ROOT, "tools", "pyi_rth_no_av.py")],
    excludes=excludes,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if keep(b)]
a.datas = [d for d in a.datas if keep(d)]
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AppLiveCaption",
    icon=os.path.join(ROOT, "app", "assets", "app.ico"),
    console=CONSOLE,
    disable_windowed_traceback=False,
    upx=False,
    version=VERSION_INFO,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AppLiveCaption",
)
