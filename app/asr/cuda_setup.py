"""Make CUDA 12 / cuDNN 9 DLLs visible to CTranslate2 on Windows.

CTranslate2 ships only the cuDNN dispatcher (``cudnn64_9.dll``); it also needs
``cublas64_12.dll`` and the cuDNN sub-libraries. They are looked up, in order:

1. next to the frozen executable (PyInstaller build),
2. in the ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` pip packages (requirements.txt),
3. in PyTorch's ``torch/lib``, if PyTorch is installed for something else,
4. in a CUDA 12.x toolkit install (``CUDA_PATH_V12_*``).

Packages are located without importing them, so start-up stays fast.
"""

from __future__ import annotations

import glob
import importlib.machinery
import logging
import os
import sys

log = logging.getLogger(__name__)

_done = False
_added: list[str] = []


def _package_dirs(name: str) -> list[str]:
    """Folders of an installed top-level package, found without importing it.

    (``importlib.util.find_spec`` would fail for torch, which the app blocks in
    ``sys.modules``.)
    """
    spec = importlib.machinery.PathFinder.find_spec(name)
    return list(spec.submodule_search_locations or []) if spec else []


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    if getattr(sys, "frozen", False):
        dirs.append(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    for root in _package_dirs("nvidia"):
        dirs += glob.glob(os.path.join(root, "cublas", "bin"))
        dirs += glob.glob(os.path.join(root, "cudnn", "bin"))
    for root in _package_dirs("torch"):
        dirs.append(os.path.join(root, "lib"))
    for key, value in sorted(os.environ.items()):
        if key.upper().startswith("CUDA_PATH_V12"):
            dirs.append(os.path.join(value, "bin"))
    return dirs


def setup_cuda_dlls() -> list[str]:
    """Register DLL directories that contain cuBLAS 12 or cuDNN 9. Safe to call repeatedly."""
    global _done
    if _done or sys.platform != "win32":
        return _added
    _done = True
    for d in _candidate_dirs():
        has_libs = any(os.path.isfile(os.path.join(d, f)) for f in ("cublas64_12.dll", "cudnn_ops64_9.dll"))
        if has_libs and d not in _added:
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
