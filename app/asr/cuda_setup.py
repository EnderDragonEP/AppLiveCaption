"""Make CUDA 12 / cuDNN 9 DLLs visible to CTranslate2 on Windows.

CTranslate2 ships only the cuDNN dispatcher (``cudnn64_9.dll``); it also needs
``cublas64_12.dll`` and the cuDNN sub-libraries. They are looked up, in order:

1. next to the frozen executable (PyInstaller build),
2. in PyTorch's ``torch/lib`` (the CUDA 12.x wheel bundles matching versions),
3. in ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` pip packages,
4. in a CUDA 12.x toolkit install (``CUDA_PATH_V12_*``).

Torch is located without importing it, so start-up stays fast.
"""

from __future__ import annotations

import glob
import importlib.util
import logging
import os
import sys

log = logging.getLogger(__name__)

_done = False
_added: list[str] = []


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        dirs += [base, os.path.join(base, "torch", "lib")]
    try:
        spec = importlib.util.find_spec("torch")
        if spec and spec.origin:
            dirs.append(os.path.join(os.path.dirname(spec.origin), "lib"))
    except (ImportError, ValueError):
        pass
    try:
        spec = importlib.util.find_spec("nvidia")
        for root in (spec.submodule_search_locations or []) if spec else []:
            dirs += glob.glob(os.path.join(root, "cublas", "bin"))
            dirs += glob.glob(os.path.join(root, "cudnn", "bin"))
    except (ImportError, ValueError):
        pass
    for key, value in sorted(os.environ.items()):
        if key.upper().startswith("CUDA_PATH_V12"):
            dirs.append(os.path.join(value, "bin"))
    return dirs


def setup_cuda_dlls() -> list[str]:
    """Register DLL directories that contain cuBLAS 12. Safe to call repeatedly."""
    global _done
    if _done or sys.platform != "win32":
        return _added
    _done = True
    for d in _candidate_dirs():
        if os.path.isfile(os.path.join(d, "cublas64_12.dll")) and d not in _added:
            try:
                os.add_dll_directory(d)
            except OSError:
                continue
            # cuDNN loads its sub-libraries with plain LoadLibrary, which
            # ignores add_dll_directory, so PATH is extended as well.
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            _added.append(d)
    if _added:
        log.info("CUDA DLL directories: %s", _added)
    else:
        log.info("No CUDA 12 runtime found; GPU inference will be unavailable")
    return _added
