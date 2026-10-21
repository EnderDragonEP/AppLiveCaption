"""NVIDIA's CUDA libraries for GPU inference, downloaded on first use.

The built app doesn't ship them, which keeps it about 1.3 GB smaller. On a PC
with an NVIDIA GPU the user downloads them once from the Models page: NVIDIA's
own pip packages (wheels) from PyPI, about 1 GB. Each wheel's SHA-256 is
checked against the value pinned here before anything is unpacked, and only
the DLLs CTranslate2 loads are kept, in %LOCALAPPDATA%\\AppLiveCaption\\cuda.

The versions must match the cuDNN loader that CTranslate2 bundles (9.1 for
CTranslate2 4.6) and requirements.txt.
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


def lib_dir() -> Path:
    from ..paths import local_dir

    return local_dir() / "cuda"


def installed_dir() -> Path | None:
    """The folder with a complete, current set of libraries, or None."""
    d = lib_dir()
    try:
        meta = json.loads((d / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("bundle") != BUNDLE_ID or (d / REMOVE_FLAG).exists():
        return None
    files = meta.get("files", {})
    for name, size in files.items():
        p = d / name
        if not p.is_file() or p.stat().st_size != size:
            return None
    return d if files else None


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


def _fetch(wheel: Wheel, dest: Path, done_before: int, on_progress: ProgressCallback, cancel: threading.Event) -> None:
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
                        on_progress(done_before + written, DOWNLOAD_SIZE, wheel.name)
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


def download(on_progress: ProgressCallback, cancel: threading.Event) -> Path:
    """Download, verify and unpack every wheel. Resumable; returns the library folder.

    Raises DownloadCancelled or DownloadError.
    """
    target = lib_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / REMOVE_FLAG).unlink(missing_ok=True)
    (target / MARKER).unlink(missing_ok=True)
    cache = target / "downloads"
    cache.mkdir(exist_ok=True)
    # Room for the unpacked DLLs plus the largest wheel (each is deleted after unpacking).
    need = INSTALLED_SIZE + max(w.size for w in WHEELS) + DISK_MARGIN
    free = shutil.disk_usage(target).free
    if free < need:
        raise DownloadError("disk_space", f"need {need / 2**30:.1f} GB, {free / 2**30:.1f} GB free")

    files: dict[str, int] = {}
    done = 0
    for wheel in WHEELS:
        path = cache / os.path.basename(wheel.url)
        _fetch(wheel, path, done, on_progress, cancel)
        if path.stat().st_size != wheel.size or _sha256(path, cancel) != wheel.sha256:
            path.unlink(missing_ok=True)  # don't resume from a bad file next time
            raise DownloadError("corrupt", f"{wheel.name} {wheel.version} failed its checksum")
        try:
            files.update(_extract(wheel, path, target))
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            kind = "io" if isinstance(exc, OSError) else "corrupt"
            raise DownloadError(kind, f"{wheel.name}: {exc}") from exc
        path.unlink(missing_ok=True)
        done += wheel.size
        on_progress(done, DOWNLOAD_SIZE, wheel.name)
    shutil.rmtree(cache, ignore_errors=True)
    (target / MARKER).write_text(json.dumps({"bundle": BUNDLE_ID, "files": files}, indent=1), encoding="utf-8")
    log.info("CUDA libraries installed in %s", target)
    return target
