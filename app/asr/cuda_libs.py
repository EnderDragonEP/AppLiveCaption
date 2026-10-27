"""NVIDIA's CUDA libraries for GPU inference, downloaded on first use.

The built app doesn't ship them, which keeps it about 1.3 GB smaller. On a PC
with an NVIDIA GPU the user downloads them once from the Models page: NVIDIA's
own pip packages (wheels) from PyPI, about 1 GB. Each wheel's SHA-256 is
checked against the value pinned here before anything is unpacked, and only
the DLLs CTranslate2 loads are kept, in the ``cuda`` folder next to the exe.

The versions must match the cuDNN loader that CTranslate2 bundles (9.1 for
CTranslate2 4.6) and requirements.txt.

Maxwell GPUs (GTX 900 series and older GTX 700/800 models) also need the
"legacy engine": a build of the app's CTranslate2 version with GPU code for
them (tools/build_legacy_ctranslate2.ps1), from this project's GitHub release,
kept in ``cuda/legacy`` (app/asr/cuda_setup.py loads it instead of the bundled
ctranslate2.dll).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
MARKER = ".alc_cuda.json"
REMOVE_FLAG = ".remove"
DISK_MARGIN = 300 * 1024 * 1024


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    url: str
    sha256: str
    size: int
    files: tuple[str, ...]  # DLLs to keep, as paths inside the wheel


# Pinned from https://pypi.org/pypi/<name>/<version>/json (win_amd64 wheels).
# Left out: cuDNN's own loader cudnn64_9.dll (CTranslate2 bundles it), cuDNN's
# RNN/attention library cudnn_adv64_9.dll and nvblas64_12.dll, never loaded.
WHEELS = (
    Wheel(
        "nvidia-cublas-cu12", "12.4.5.8",
        "https://files.pythonhosted.org/packages/e2/2a/4f27ca96232e8b5269074a72e03b4e0d43aa68c9b965058b1684d07c6ff8/"
        "nvidia_cublas_cu12-12.4.5.8-py3-none-win_amd64.whl",
        "5a796786da89203a0657eda402bcdcec6180254a8ac22d72213abc42069522dc", 396895858,
        ("nvidia/cublas/bin/cublas64_12.dll", "nvidia/cublas/bin/cublasLt64_12.dll"),
    ),
    Wheel(
        "nvidia-cudnn-cu12", "9.1.0.70",
        "https://files.pythonhosted.org/packages/3f/d0/f90ee6956a628f9f04bf467932c0a25e5a7e706a684b896593c06c82f460/"
        "nvidia_cudnn_cu12-9.1.0.70-py3-none-win_amd64.whl",
        "6278562929433d68365a07a4a1546c237ba2849852c0d4b2262a486e805b977a", 679925892,
        tuple(f"nvidia/cudnn/bin/cudnn_{n}64_9.dll" for n in
              ("ops", "cnn", "graph", "heuristic", "engines_precompiled", "engines_runtime_compiled")),
    ),
    Wheel(
        "nvidia-cuda-nvrtc-cu12", "12.4.127",
        "https://files.pythonhosted.org/packages/7c/30/8c844bfb770f045bcd8b2c83455c5afb45983e1a8abf0c4e5297b481b6a5/"
        "nvidia_cuda_nvrtc_cu12-12.4.127-py3-none-win_amd64.whl",
        "a961b2f1d5f17b14867c619ceb99ef6fcec12e46612711bcec78eb05068a60ec", 19751955,
        ("nvidia/cuda_nvrtc/bin/nvrtc64_120_0.dll", "nvidia/cuda_nvrtc/bin/nvrtc-builtins64_124.dll"),
    ),
)

# Changes whenever a pinned version changes, so an older set is replaced.
BUNDLE_ID = "+".join(f"{w.name}=={w.version}" for w in WHEELS)
DOWNLOAD_SIZE = sum(w.size for w in WHEELS)
INSTALLED_SIZE = 1_420_480_240  # the kept DLLs, unpacked

# The legacy engine: CTranslate2 4.6.2's ctranslate2.dll built with GPU code for compute capability 5.0 and 5.2
# added. It stands in for exactly one official ctranslate2.dll (the one bundled with the app, by its SHA-256);
# with another CTranslate2 version it isn't used.
LEGACY_ENGINE = Wheel(
    "ctranslate2-legacy-gpu", "4.6.2",
    "https://github.com/EnderDragonEP/AppLiveCaption/releases/download/legacy-gpu-ct2-4.6.2/"
    "ctranslate2-4.6.2-maxwell-win_amd64.zip",
    "6773ffd13c98449132e0653325df6eccd857a205ad9b9ceb33c33a9171d798c8", 19683483,
    ("ctranslate2.dll", "LICENSE"),
)
LEGACY_REPLACES_SHA256 = "f73233e1d637c95d644d329a8a9c90c237060f0cd9dcecca88e90da5775c4355"  # ctranslate2 4.6.2
LEGACY_INSTALLED_SIZE = 61_761_649  # ctranslate2.dll and its license, unpacked
LEGACY_ID = f"{LEGACY_ENGINE.name}=={LEGACY_ENGINE.version}"


def lib_dir() -> Path:
    from ..paths import local_data_dir

    return local_data_dir() / "cuda"


def legacy_dir() -> Path:
    return lib_dir() / "legacy"


def legacy_available() -> bool:
    """The legacy engine can be downloaded (its release is pinned)."""
    return bool(LEGACY_ENGINE.sha256)


def _complete(d: Path, bundle: str) -> bool:
    """``d`` holds every file its marker lists, at the listed size, for ``bundle``."""
    try:
        meta = json.loads((d / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if meta.get("bundle") != bundle or (lib_dir() / REMOVE_FLAG).exists():
        return False
    files = meta.get("files", {})
    return bool(files) and all((d / n).is_file() and (d / n).stat().st_size == size for n, size in files.items())


def installed_dir() -> Path | None:
    """The folder with a complete, current set of libraries, or None."""
    d = lib_dir()
    return d if _complete(d, BUNDLE_ID) else None


def legacy_dll() -> Path | None:
    """The downloaded legacy engine's ctranslate2.dll, when it's complete and current."""
    d = legacy_dir()
    return d / "ctranslate2.dll" if _complete(d, LEGACY_ID) else None


def disk_usage() -> int:
    d = lib_dir()
    return sum(p.stat().st_size for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def has_partial_download() -> bool:
    """Files from an interrupted download are there, but not a complete set."""
    d = lib_dir()
    return d.is_dir() and installed_dir() is None and not removal_scheduled() and any(d.iterdir())


def removal_scheduled() -> bool:
    return (lib_dir() / REMOVE_FLAG).exists()


def remove(in_use: bool) -> None:
    """Delete the libraries, or mark them for deletion at the next start when
    this process may have loaded them: Windows locks loaded DLLs, and deleting
    only some of them would crash cuDNN when it loads the rest."""
    d = lib_dir()
    if in_use:
        (d / REMOVE_FLAG).write_text("", encoding="utf-8")
    else:
        shutil.rmtree(d, ignore_errors=True)


def cancel_removal() -> None:
    (lib_dir() / REMOVE_FLAG).unlink(missing_ok=True)


def remove_if_scheduled() -> None:
    """Called at start-up, before any CUDA library is loaded."""
    d = lib_dir()
    if (d / REMOVE_FLAG).exists():
        shutil.rmtree(d, ignore_errors=True)
        log.info("Removed the CUDA libraries in %s", d)


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "network" | "disk_space" | "io" | "corrupt"


ProgressCallback = Callable[[int, int, str], None]  # bytes done, total, current file


def _fetch(wheel: Wheel, dest: Path, done_before: int, total: int, on_progress: ProgressCallback,
           cancel: threading.Event) -> None:
    """Download one wheel to ``dest``, resuming a partial file; retries on network errors."""
    attempts = 0
    while True:
        if cancel.is_set():
            raise DownloadCancelled()
        offset = dest.stat().st_size if dest.exists() else 0
        if offset >= wheel.size:
            return
        req = urllib.request.Request(wheel.url, headers={"User-Agent": "AppLiveCaption"})
        if offset:
            req.add_header("Range", f"bytes={offset}-")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if offset and resp.status != 206:
                    offset = 0  # the server ignored the range: start over
                with open(dest, "ab" if offset else "wb") as fh:
                    written = offset
                    while chunk := resp.read(CHUNK):
                        if cancel.is_set():
                            raise DownloadCancelled()
                        fh.write(chunk)
                        written += len(chunk)
                        on_progress(done_before + written, total, wheel.name)
            return
        except DownloadCancelled:
            raise
        except OSError as exc:
            if isinstance(exc, PermissionError) or getattr(exc, "errno", None) == 28:
                raise DownloadError("io", str(exc)) from exc
            attempts += 1
            log.warning("download of %s interrupted (%s), retry %d", wheel.name, exc, attempts)
            if attempts > 5:
                raise DownloadError("network", str(exc)) from exc
            if cancel.wait(min(10, 2 ** attempts)):
                raise DownloadCancelled() from None


def _sha256(path: Path, cancel: threading.Event) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(8 * CHUNK):
            if cancel.is_set():
                raise DownloadCancelled()
            h.update(chunk)
    return h.hexdigest()


def _extract(wheel: Wheel, path: Path, target: Path) -> dict[str, int]:
    """Unpack the wheel's kept DLLs into ``target`` (flat) and return their sizes."""
    sizes = {}
    with zipfile.ZipFile(path) as zf:
        for member in wheel.files:
            info = zf.getinfo(member)  # KeyError if the wheel isn't what we pinned
            name = os.path.basename(member)
            part = target / (name + ".part")
            with zf.open(info) as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst, CHUNK)
            os.replace(part, target / name)
            sizes[name] = info.file_size
    return sizes


def to_download(legacy: bool) -> list[tuple[Wheel, Path, str]]:
    """What a download still has to fetch: (wheel, folder it's unpacked to, bundle id) for each wheel.
    ``legacy``: the GPU needs the legacy engine too (then CUDA libraries found elsewhere on the PC do)."""
    from .cuda_setup import cuda_libraries_found

    have_cuda = installed_dir() is not None or (legacy and cuda_libraries_found())
    parts = [] if have_cuda else [(w, lib_dir(), BUNDLE_ID) for w in WHEELS]
    if legacy and legacy_available() and legacy_dll() is None:
        parts.append((LEGACY_ENGINE, legacy_dir(), LEGACY_ID))
    return parts


def download_size(legacy: bool) -> int:
    return sum(w.size for w, _d, _b in to_download(legacy))


def download(on_progress: ProgressCallback, cancel: threading.Event, legacy: bool = False) -> Path:
    """Download, verify and unpack every wheel (and, with ``legacy``, the legacy engine) that isn't installed
    yet. Resumable; returns the library folder.

    Raises DownloadCancelled or DownloadError.
    """
    target = lib_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / REMOVE_FLAG).unlink(missing_ok=True)
    parts = to_download(legacy)
    cache = target / "downloads"
    cache.mkdir(exist_ok=True)
    # Room for the unpacked DLLs plus the largest wheel (each is deleted after unpacking).
    unpacked = sum(INSTALLED_SIZE if w is not LEGACY_ENGINE else LEGACY_INSTALLED_SIZE for w, _d, _b in parts)
    need = unpacked + max((w.size for w, _d, _b in parts), default=0) + DISK_MARGIN
    free = shutil.disk_usage(target).free
    if parts and free < need:
        raise DownloadError("disk_space", f"need {need / 2**30:.1f} GB, {free / 2**30:.1f} GB free")

    total = sum(w.size for w, _d, _b in parts)
    files: dict[Path, dict[str, int]] = {}
    done = 0
    for wheel, folder, _bundle in parts:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / MARKER).unlink(missing_ok=True)
        path = cache / os.path.basename(wheel.url)
        _fetch(wheel, path, done, total, on_progress, cancel)
        if path.stat().st_size != wheel.size or _sha256(path, cancel) != wheel.sha256:
            path.unlink(missing_ok=True)  # don't resume from a bad file next time
            raise DownloadError("corrupt", f"{wheel.name} {wheel.version} failed its checksum")
        try:
            files.setdefault(folder, {}).update(_extract(wheel, path, folder))
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            kind = "io" if isinstance(exc, OSError) else "corrupt"
            raise DownloadError(kind, f"{wheel.name}: {exc}") from exc
        path.unlink(missing_ok=True)
        done += wheel.size
        on_progress(done, total, wheel.name)
    shutil.rmtree(cache, ignore_errors=True)
    for folder, bundle in {(f, b) for _w, f, b in parts}:
        (folder / MARKER).write_text(json.dumps({"bundle": bundle, "files": files[folder]}, indent=1), encoding="utf-8")
    log.info("CUDA libraries installed in %s", target)
    return target
