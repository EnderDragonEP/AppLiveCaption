# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for AppLiveCaption.

    pyinstaller AppLiveCaption.spec --noconfirm

Output: dist/AppLiveCaption/AppLiveCaption.exe. Models are not bundled; the
app downloads them into %LOCALAPPDATA%\\AppLiveCaption\\models.

Environment switches:
    ALC_INCLUDE_TORCH=0   leave out PyTorch + transformers (no Breeze ASR 25, much
                          smaller build). GPU inference for faster-whisper then needs
                          the nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels installed
                          at build time; they are bundled automatically if present.
    ALC_CONSOLE=1         keep a console window for debugging.
"""

import glob
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = os.path.abspath(SPECPATH)  # noqa: F821 - defined by PyInstaller
sys.path.insert(0, os.path.join(ROOT, "tools"))
from build_excludes import unused_modules  # noqa: E402
INCLUDE_TORCH = os.environ.get("ALC_INCLUDE_TORCH", "1") != "0"
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

binaries = collect_dynamic_libs("ctranslate2")  # ctranslate2.dll, cudnn64_9.dll, libiomp5md.dll

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
]

if INCLUDE_TORCH:
    hiddenimports += [
        "transformers.models.whisper",
        "transformers.models.whisper.modeling_whisper",
        "transformers.models.whisper.processing_whisper",
        "transformers.models.whisper.feature_extraction_whisper",
        "transformers.models.whisper.tokenization_whisper",
        "transformers.models.whisper.configuration_whisper",
        "transformers.models.whisper.generation_whisper",
    ]
    datas += copy_metadata("transformers") + copy_metadata("safetensors") + copy_metadata("torch")
    datas += collect_data_files("transformers", include_py_files=False)
else:
    excludes += ["torch", "transformers", "torchvision", "torchaudio"]
    # CUDA 12 runtime for CTranslate2 from the NVIDIA pip wheels, if installed.
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            binaries += [(src, ".") for src, _dst in collect_dynamic_libs(pkg)]
        except Exception:  # noqa: BLE001 - optional
            pass

# Leave out installed packages the app doesn't depend on. Built with a Python
# shared with other projects, PyInstaller would otherwise follow optional
# imports into them (transformers -> datasets, accelerate, torchvision, ...).
_unused = unused_modules(
    Path(ROOT) / "requirements.txt",
    drop=() if INCLUDE_TORCH else ("torch", "transformers", "safetensors"),
    extra_roots=() if INCLUDE_TORCH else ("nvidia-cublas-cu12", "nvidia-cudnn-cu12"),
)
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
    version=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AppLiveCaption",
)
