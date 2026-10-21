# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for AppLiveCaption.

    pyinstaller AppLiveCaption.spec --noconfirm

Output: dist/AppLiveCaption/AppLiveCaption.exe. Models are not bundled; the
app downloads them into %LOCALAPPDATA%\\AppLiveCaption\\models.

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

binaries = collect_dynamic_libs("ctranslate2")  # ctranslate2.dll, cudnn64_9.dll (cuDNN's loader), libiomp5md.dll

# NVIDIA's CUDA 12 libraries for running on the GPU, from the nvidia-* pip packages.
# Left out: cuDNN's own loader (CTranslate2 bundles the same one), cuDNN's
# "adv" library (RNN/attention kernels CTranslate2 never loads, 230 MB) and the
# nvblas BLAS wrapper.
# They keep their package folders (nvidia/cublas/bin, ...): PyInstaller also
# collects cuBLAS and cuDNN as dependencies of the other cuDNN DLLs and puts
# those copies there, so any other destination would bundle them twice
# (an extra 550 MB). app/asr/cuda_setup.py adds these folders to the DLL path.
CUDA_SKIP = {"cudnn64_9.dll", "cudnn_adv64_9.dll", "nvblas64_12.dll"}
_cuda = [(src, dst) for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc")
         for src, dst in collect_dynamic_libs(pkg) if os.path.basename(src).lower() not in CUDA_SKIP]
if not any(os.path.basename(src).lower() == "cublas64_12.dll" for src, _ in _cuda):
    raise SystemExit("NVIDIA's CUDA packages are missing; run: pip install -r requirements.txt")
binaries += _cuda

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
]

# Qt files a widgets-only English app never loads (as in HorizontalScroll's spec):
# the software OpenGL renderer, Qt's own translations, unused image formats and
# plugins. The on-screen keyboard plugin alone pulls in Qt Quick, QML and OpenGL.
# The update check uses HTTPS through Windows' TLS (qschannelbackend), so Qt's
# OpenSSL backends can go; model downloads use Python's own ssl module.
DROP_FILES = {
    "opengl32sw.dll", "d3dcompiler_47.dll", "qt6pdf.dll", "qopensslbackend.dll", "qcertonlybackend.dll",
    "qt6quick.dll", "qt6qml.dll", "qt6qmlmodels.dll", "qt6qmlmeta.dll", "qt6qmlworkerscript.dll",
    "qt6opengl.dll", "qt6virtualkeyboard.dll",
}
DROP_PLUGIN_DIRS = {"networkinformation", "platforminputcontexts", "generic"}
DROP_IMAGE_PLUGINS = {"qgif", "qjpeg", "qtiff", "qwebp", "qicns", "qtga", "qwbmp", "qpdf"}


def keep(entry) -> bool:
    dest = PurePath(entry[0])
    if dest.name.lower() in DROP_FILES or "translations" in dest.parts:
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
    runtime_hooks=[],
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
