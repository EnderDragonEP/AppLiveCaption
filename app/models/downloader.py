"""Model downloads from the Hugging Face Hub.

File lists, URLs, auth headers and the HTTP session come from
``huggingface_hub``; the transfer loop is our own so downloads can report
bytes/speed, be cancelled at any moment and resume from a ``.part`` file
after an interruption.

The app runs with ``HF_HUB_OFFLINE=1`` so nothing else ever reaches the
network; the downloader lifts that flag only while it is working.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .catalog import ModelInfo
from .storage import PARTIAL_SUFFIX, model_path, models_dir, write_marker

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
DISK_MARGIN = 300 * 1024 * 1024  # keep some free space beyond the download itself

_net_lock = threading.Lock()
_net_users = 0


@contextmanager
def allow_network():
    """Temporarily lift HF_HUB_OFFLINE for huggingface_hub requests made by the downloader."""
    global _net_users
    from huggingface_hub import constants

    with _net_lock:
        _net_users += 1
        constants.HF_HUB_OFFLINE = False
    try:
        yield
    finally:
        with _net_lock:
            _net_users -= 1
            if _net_users == 0:
                constants.HF_HUB_OFFLINE = os.environ.get("HF_HUB_OFFLINE", "") in ("1", "true", "True", "ON", "YES")


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "network" | "disk_space" | "not_found" | "io"


@dataclass
class Progress:
    done: int
    total: int
    speed: float  # bytes per second
    file: str


ProgressCallback = Callable[[Progress], None]


def _selected_files(info: ModelInfo, siblings: list[tuple[str, int]]) -> list[tuple[str, int]]:
    out = []
    for name, size in siblings:
        if info.files:
            if not any(fnmatch.fnmatch(name, pat) for pat in info.files):
                continue
        elif any(fnmatch.fnmatch(name, pat) for pat in info.ignore_patterns):
            continue
        out.append((name, size or 0))
    return out


class _Speed:
    """Moving-average transfer speed over the last few seconds."""

    def __init__(self, window: float = 3.0):
        self.window = window
        self.samples: list[tuple[float, int]] = []

    def add(self, total_done: int) -> float:
        now = time.monotonic()
        self.samples.append((now, total_done))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.pop(0)
        t0, b0 = self.samples[0]
        return (total_done - b0) / (now - t0) if now > t0 else 0.0


def download_model(info: ModelInfo, on_progress: ProgressCallback, cancel: threading.Event,
                   base: Path | None = None) -> Path:
    """Download (or resume) every file of ``info``. Returns the model folder.

    Raises DownloadCancelled or DownloadError.
    """
    from huggingface_hub import HfApi, hf_hub_url
    from huggingface_hub.errors import RepositoryNotFoundError
    from huggingface_hub.utils import build_hf_headers, get_session

    base = base or models_dir()
    target = model_path(info, base)
    target.mkdir(parents=True, exist_ok=True)

    with allow_network():
        try:
            meta = HfApi().model_info(info.repo, files_metadata=True, timeout=30)
        except RepositoryNotFoundError as exc:
            raise DownloadError("not_found", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network errors of all kinds
            raise DownloadError("network", str(exc)) from exc
        files = _selected_files(info, [(s.rfilename, s.size or 0) for s in meta.siblings or []])
        if not files:
            raise DownloadError("not_found", f"no files in {info.repo}")
        total = sum(size for _, size in files)

        def have(name: str, size: int) -> int:
            final = target / name
            if final.is_file() and final.stat().st_size == size:
                return size
            part = target / (name + PARTIAL_SUFFIX)
            return part.stat().st_size if part.is_file() else 0

        done = sum(have(n, s) for n, s in files)
        free = shutil.disk_usage(target).free
        if total - done + DISK_MARGIN > free:
            raise DownloadError("disk_space", f"need {(total - done) / 2**30:.1f} GB, {free / 2**30:.1f} GB free")

        session = get_session()
        headers = build_hf_headers()
        speed = _Speed()
        on_progress(Progress(done, total, 0.0, ""))

        for name, size in files:
            final = target / name
            if final.is_file() and final.stat().st_size == size:
                continue
            final.parent.mkdir(parents=True, exist_ok=True)
            part = target / (name + PARTIAL_SUFFIX)
            url = hf_hub_url(meta.id, name, revision=meta.sha)
            others = sum(have(n, s) for n, s in files if n != name)
            attempts = 0
            while True:
                if cancel.is_set():
                    raise DownloadCancelled()
                offset = part.stat().st_size if part.is_file() else 0
                if size and offset > size:
                    part.unlink()
                    offset = 0
                req_headers = dict(headers)
                if offset:
                    req_headers["Range"] = f"bytes={offset}-"
                try:
                    with session.stream("GET", url, headers=req_headers, timeout=30) as resp:
                        if resp.status_code == 416:  # nothing left to fetch
                            break
                        if resp.status_code not in (200, 206):
                            raise DownloadError("network", f"HTTP {resp.status_code} for {name}")
                        if resp.status_code == 200:
                            offset = 0  # the server ignored the range: start the file over
                        with open(part, "ab" if offset else "wb") as fh:
                            written = offset
                            for chunk in resp.iter_bytes(CHUNK):
                                if cancel.is_set():
                                    raise DownloadCancelled()
                                fh.write(chunk)
                                written += len(chunk)
                                cur = others + written
                                on_progress(Progress(cur, total, speed.add(cur), name))
                    break
                except (DownloadCancelled, DownloadError):
                    raise
                except Exception as exc:  # noqa: BLE001 - httpx errors, timeouts, socket errors
                    if isinstance(exc, PermissionError) or (isinstance(exc, OSError) and exc.errno == 28):
                        raise DownloadError("io", str(exc)) from exc  # access denied / disk full
                    attempts += 1
                    log.warning("download of %s interrupted (%s), retry %d", name, exc, attempts)
                    if attempts > 5:
                        raise DownloadError("network", str(exc)) from exc
                    if cancel.wait(min(10, 2 ** attempts)):
                        raise DownloadCancelled() from None
            if size and part.is_file() and part.stat().st_size != size:
                raise DownloadError("network", f"{name} is incomplete")
            if part.is_file():
                os.replace(part, final)

    write_marker(info, {n: s for n, s in files}, base)
    on_progress(Progress(total, total, 0.0, ""))
    log.info("Downloaded %s to %s", info.repo, target)
    return target
