"""Make CUDA 12 / cuDNN 9 DLLs visible to CTranslate2 on Windows.

CTranslate2 ships only the cuDNN loader (``cudnn64_9.dll``); it also needs
``cublas64_12.dll`` and the cuDNN sub-libraries. They are looked up, in order:

1. in the ``cuda`` folder next to the exe, where the app downloads them when
   the user asks (``app/asr/cuda_libs.py``; the built app doesn't ship them),
2. in NVIDIA's ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` pip packages,
3. in PyTorch's ``torch/lib``, if PyTorch is installed for something else,
4. in a CUDA 12.x toolkit install (``CUDA_PATH_V12_*``).

Only a complete set (cuBLAS and cuDNN) counts: with either missing, cuDNN
aborts the whole process when a model is loaded on the GPU, so the app then
stays on the CPU. Packages are located without importing them, so start-up
stays fast.

On a Maxwell GPU, which CTranslate2's official build has no GPU code for, the
downloaded legacy engine (app/asr/cuda_libs.py) is loaded in place of the
bundled ctranslate2.dll, before CTranslate2 is first imported.
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import importlib.machinery
import logging
import os
import sys

log = logging.getLogger(__name__)

_done = False
_added: list[str] = []
_complete = False
_legacy_loaded = False


def _package_dirs(name: str) -> list[str]:
    """Folders of an installed top-level package, found without importing it.

    (``importlib.util.find_spec`` would fail for torch, which the app blocks in
    ``sys.modules``.)
    """
    spec = importlib.machinery.PathFinder.find_spec(name)
    return list(spec.submodule_search_locations or []) if spec else []


def _candidate_sources() -> list[list[str]]:
    """Places that may hold the CUDA libraries, best first; each is a list of folders."""
    from .cuda_libs import installed_dir

    sources: list[list[str]] = []
    downloaded = installed_dir()
    if downloaded is not None:
        sources.append([str(downloaded)])
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


def setup_cuda_dlls(refresh: bool = False) -> list[str]:
    """Make one complete set of CUDA libraries loadable; returns its folders.

    Only the first source with both cuBLAS and cuDNN is used, so libraries from
    different installs (e.g. NVIDIA's packages and PyTorch's copies) never mix.
    Safe to call repeatedly; ``refresh`` looks again (after a download).
    """
    global _done, _complete
    if (_done and not refresh) or sys.platform != "win32":
        return _added
    _done = True
    if _complete:
        return _added  # a set is already loaded into this process; keep using it
    complete = [f for f in _candidate_sources() if _has(f, "cublas64_12.dll") and _has(f, "cudnn_ops64_9.dll")]
    if not complete:
        log.info("No complete CUDA 12 / cuDNN 9 libraries found; models run on the CPU")
        return _added
    for d in complete[0]:
        if d in _added or not os.path.isdir(d):
            continue
        try:
            os.add_dll_directory(d)
        except OSError:
            continue
        _added.append(d)
    # cuDNN loads its sub-libraries (and NVRTC) with plain LoadLibrary, which
    # ignores add_dll_directory, so PATH is extended too, keeping this order.
    os.environ["PATH"] = os.pathsep.join(_added) + os.pathsep + os.environ.get("PATH", "")
    _complete = True
    log.info("CUDA DLL directories: %s", _added)
    _load_legacy_engine()
    return _added


def _ctranslate2_dir() -> str | None:
    """The folder of CTranslate2's DLLs (in the built app, its package's modules are in the archive)."""
    if getattr(sys, "frozen", False):
        d = os.path.join(getattr(sys, "_MEIPASS", ""), "ctranslate2")
        return d if os.path.isdir(d) else None
    dirs = _package_dirs("ctranslate2")
    return dirs[0] if dirs else None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(8 * 2**20):
            h.update(chunk)
    return h.hexdigest()


def _load_legacy_engine() -> None:
    """On a Maxwell GPU with the legacy engine downloaded, make CTranslate2 use it.

    CTranslate2's package loads every DLL in its folder by path, then imports its extension, which links
    ctranslate2.dll by name; Windows resolves that to the ctranslate2.dll already loaded. So the legacy one is
    loaded first, and the package is imported without loading its own (which would then be a second copy).
    """
    global _legacy_loaded
    if "ctranslate2" in sys.modules:
        return  # (too late in this process: it's used after a restart)
    from .cuda_libs import LEGACY_REPLACES_SHA256, legacy_dll
    from .hardware import needs_legacy_engine, nvidia_gpu

    dll = legacy_dll()
    if dll is None or not needs_legacy_engine(nvidia_gpu()):
        return
    package = _ctranslate2_dir()
    official = os.path.join(package, "ctranslate2.dll") if package else ""
    if not os.path.isfile(official) or _sha256(official) != LEGACY_REPLACES_SHA256:
        log.warning("The legacy GPU engine was built for another version of CTranslate2; not using it")
        return
    try:
        os.add_dll_directory(package)  # (its libiomp5md.dll)
        ctypes.CDLL(str(dll))
        real_glob = glob.glob
        glob.glob = lambda pattern, *a, **k: [f for f in real_glob(pattern, *a, **k)
                                              if os.path.basename(f).lower() != "ctranslate2.dll"]
        try:
            import ctranslate2  # noqa: F401  (binds to the legacy engine loaded above)
        finally:
            glob.glob = real_glob
    except Exception:  # noqa: BLE001
        log.exception("Couldn't load the legacy GPU engine")
        return
    _legacy_loaded = True
    log.info("Using the legacy GPU engine %s", dll)


def legacy_engine_loaded() -> bool:
    """CTranslate2 runs on the legacy engine in this process."""
    return _legacy_loaded


def active_dirs() -> list[str]:
    """The folders of the CUDA libraries this process uses (empty: none found)."""
    return list(_added) if _complete else []


def cuda_libraries_found() -> bool:
    """True once a complete set of CUDA libraries has been made loadable."""
    setup_cuda_dlls()
    return _complete
