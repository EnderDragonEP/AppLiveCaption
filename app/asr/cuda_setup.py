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
import importlib.machinery
import json
import logging
import os
import sys
from ctypes import wintypes

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
    # Number CUDA's devices in PCI order, as NVML lists them, so the GPU the app picks (hardware.nvidia_gpu) is
    # the one CTranslate2 runs on. (Before any CUDA call; a user's own setting is kept.)
    os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
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


def _is_official_dll(path: str) -> bool:
    """``path`` is the ctranslate2.dll the legacy engine stands in for. Hashing it takes a moment, so a match is
    remembered (by path, size and modification time) in the legacy engine's folder."""
    from .cuda_libs import LEGACY_REPLACES_SHA256, LEGACY_REPLACES_SIZE, file_sha256, legacy_dir

    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_size != LEGACY_REPLACES_SIZE:
        return False
    key = {"path": os.path.normcase(os.path.abspath(path)), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    cache = legacy_dir() / ".alc_checked.json"
    try:
        if json.loads(cache.read_text(encoding="utf-8")) == key:
            return True
    except (OSError, ValueError):
        pass
    if file_sha256(path) != LEGACY_REPLACES_SHA256:
        return False
    try:
        cache.write_text(json.dumps(key), encoding="utf-8")
    except OSError:
        pass
    return True


def _without_dll(package: str, real_glob):
    """glob.glob, except that CTranslate2's own ctranslate2.dll is left out of its package folder's listing.
    (Every other folder is listed as usual, so other code globbing meanwhile isn't affected.)"""
    package = os.path.normcase(os.path.abspath(package))

    def wrapper(pattern, *args, **kwargs):
        found = real_glob(pattern, *args, **kwargs)
        if os.path.normcase(os.path.abspath(os.path.dirname(str(pattern)))) != package:
            return found
        return [f for f in found if os.path.basename(f).lower() != "ctranslate2.dll"]

    return wrapper


def _loaded_paths(name: str) -> list[str]:
    """Full paths of the modules called ``name`` (e.g. "ctranslate2.dll") loaded in this process."""
    psapi, kernel32 = ctypes.WinDLL("psapi"), ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
    modules, needed = (wintypes.HMODULE * 4096)(), wintypes.DWORD()
    if not psapi.EnumProcessModules(kernel32.GetCurrentProcess(), modules, ctypes.sizeof(modules), ctypes.byref(needed)):
        return []
    paths = []
    for handle in modules[: min(len(modules), needed.value // ctypes.sizeof(wintypes.HMODULE))]:
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.GetModuleFileNameW(handle, buf, 1024) and os.path.basename(buf.value).lower() == name.lower():
            paths.append(buf.value)
    return paths


def _load_legacy_engine() -> None:
    """On a Maxwell GPU with the legacy engine downloaded, make CTranslate2 use it.

    CTranslate2's package loads every DLL in its folder by path, then imports its extension, which links
    ctranslate2.dll by name; Windows resolves that to the ctranslate2.dll already loaded. So the legacy one is
    loaded first, and the package is imported without loading its own (which would then be a second copy).
    """
    global _legacy_loaded
    if "ctranslate2" in sys.modules:
        return  # (too late in this process: it's used after a restart)
    from .cuda_libs import legacy_dll
    from .hardware import needs_legacy_engine, nvidia_gpu

    dll = legacy_dll()
    if dll is None or not needs_legacy_engine(nvidia_gpu()):
        return
    package = _ctranslate2_dir()
    if not package or not _is_official_dll(os.path.join(package, "ctranslate2.dll")):
        log.warning("The legacy GPU engine was built for another version of CTranslate2; not using it")
        return
    try:
        os.add_dll_directory(package)  # (its libiomp5md.dll)
        ctypes.CDLL(str(dll))
        real_glob = glob.glob
        glob.glob = _without_dll(package, real_glob)
        try:
            import ctranslate2  # noqa: F401  (binds to the legacy engine loaded above)
        finally:
            glob.glob = real_glob
    except Exception:  # noqa: BLE001
        log.exception("Couldn't load the legacy GPU engine")
        return
    # Only use the GPU on it if CTranslate2 really runs on it (not, say, on a second copy of its own DLL, should a
    # later version load its DLLs another way): otherwise hardware.engine_runs_on keeps this GPU on the CPU.
    loaded = _loaded_paths("ctranslate2.dll")
    if len(loaded) != 1 or not os.path.samefile(loaded[0], dll):
        log.warning("CTranslate2 didn't load the legacy GPU engine alone (%s); models run on the CPU", loaded)
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
