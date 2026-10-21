"""Make CUDA 12 / cuDNN 9 DLLs visible to CTranslate2 on Windows.

CTranslate2 ships only the cuDNN dispatcher (``cudnn64_9.dll``); it also needs
``cublas64_12.dll`` and the cuDNN sub-libraries. They are looked up, in order:

1. in the frozen app's folder (PyInstaller build; NVIDIA's DLLs keep their
   package folders there, nvidia/<library>/bin),
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


def _candidate_sources() -> list[list[str]]:
    """Places that may hold the CUDA libraries, best first; each is a list of folders."""
    sources: list[list[str]] = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        sources.append([base, *sorted(glob.glob(os.path.join(base, "nvidia", "*", "bin")))])
    for root in _package_dirs("nvidia"):
        sources.append([os.path.join(root, lib, "bin") for lib in ("cublas", "cudnn", "cuda_nvrtc")])
    for root in _package_dirs("torch"):
        sources.append([os.path.join(root, "lib")])
    for key, value in sorted(os.environ.items()):
        if key.upper().startswith("CUDA_PATH_V12"):
            sources.append([os.path.join(value, "bin")])
    return sources


def _has(folders: list[str], dll: str) -> bool:
    return any(os.path.isfile(os.path.join(d, dll)) for d in folders)


def setup_cuda_dlls() -> list[str]:
    """Make one complete set of CUDA libraries loadable. Safe to call repeatedly.

    Only the first source with both cuBLAS and cuDNN is used, so libraries from
    different installs (e.g. NVIDIA's packages and PyTorch's copies) never mix.
    """
    global _done
    if _done or sys.platform != "win32":
        return _added
    _done = True
    sources = _candidate_sources()
    complete = [f for f in sources if _has(f, "cublas64_12.dll") and _has(f, "cudnn_ops64_9.dll")]
    # Without a complete set, take what there is (e.g. a CUDA Toolkit without cuDNN).
    folders = complete[0] if complete else [d for f in sources for d in f]
    for d in folders:
        if d in _added or not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)
        except OSError:
            continue
        _added.append(d)
    # cuDNN loads its sub-libraries (and NVRTC) with plain LoadLibrary, which
    # ignores add_dll_directory, so PATH is extended too, keeping this order.
    if _added:
        os.environ["PATH"] = os.pathsep.join(_added) + os.pathsep + os.environ.get("PATH", "")
    if _added:
        log.info("CUDA DLL directories: %s", _added)
    else:
        log.info("No CUDA 12 runtime found; GPU inference will be unavailable")
    return _added
