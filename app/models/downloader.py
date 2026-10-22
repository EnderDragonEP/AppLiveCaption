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

from .catalog import _DEFAULT_IGNORE, ModelInfo
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


def select_files(siblings: list[tuple[str, int]], allow: tuple[str, ...] = ()) -> list[tuple[str, int]]:
    """The files of a model to fetch: those matching ``allow``, or without it, everything except docs."""
    out = []
    for name, size in siblings:
        if allow:
            if not any(fnmatch.fnmatch(name, pat) for pat in allow):
                continue
        elif any(fnmatch.fnmatch(name, pat) for pat in _DEFAULT_IGNORE):
            continue
        out.append((name, size or 0))
    return out


def _selected_files(info: ModelInfo, siblings: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return select_files(siblings, info.files)


def local_files(folder: Path) -> list[tuple[str, int]]:
    """The files at the top of a model folder on this PC, with their sizes (a converted model has no sub-folders)."""
    return [(p.name, p.stat().st_size) for p in sorted(folder.iterdir()) if p.is_file() and not p.name.startswith(".")]


class TransferSpeed:
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


def _copy_local(info: ModelInfo, on_progress: ProgressCallback, cancel: threading.Event, target: Path,
                base: Path) -> Path:
    """Copy a model folder chosen by the user into the models folder (the "download" of a local model)."""
    src = Path(info.source_folder)
    if not src.is_dir():
        raise DownloadError("not_found", f"the folder {src} doesn't exist any more")
    # Only the files that were checked (and counted in the size) when the model was added.
    files = _selected_files(info, local_files(src))
    if not files:
        raise DownloadError("not_found", f"no model files in {src}")
    total = sum(size for _, size in files)
    todo = sum(size for name, size in files if not (target / name).is_file() or (target / name).stat().st_size != size)
    free = shutil.disk_usage(target).free
    if todo + DISK_MARGIN > free:
        raise DownloadError("disk_space", f"need {todo / 2**30:.1f} GB, {free / 2**30:.1f} GB free")
    speed = TransferSpeed()
    done = total - todo
    on_progress(Progress(done, total, 0.0, ""))
    for name, size in files:
        final, part = target / name, target / (name + PARTIAL_SUFFIX)
        if final.is_file() and final.stat().st_size == size:
            continue
        try:
            with open(src / name, "rb") as fin, open(part, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    if cancel.is_set():
                        raise DownloadCancelled()
                    fout.write(chunk)
                    done += len(chunk)
                    on_progress(Progress(done, total, speed.add(done), name))
            os.replace(part, final)
        except OSError as exc:
            raise DownloadError("io", str(exc)) from exc
    write_marker(info, dict(files), base)
    on_progress(Progress(total, total, 0.0, ""))
    log.info("Copied %s to %s", src, target)
    return target


def download_model(info: ModelInfo, on_progress: ProgressCallback, cancel: threading.Event,
                   base: Path | None = None) -> Path:
    """Download (or resume) every file of ``info``. Returns the model folder.

    A model added from a local folder is copied instead. Raises DownloadCancelled or DownloadError.
    """
    from huggingface_hub import HfApi, hf_hub_url
    from huggingface_hub.errors import RepositoryNotFoundError
    from huggingface_hub.utils import build_hf_headers, get_session

    base = base or models_dir()
    target = model_path(info, base)
    target.mkdir(parents=True, exist_ok=True)
    if info.source_folder:
        return _copy_local(info, on_progress, cancel, target, base)

    with allow_network():
        try:
            meta = HfApi().model_info(info.repo, files_metadata=True, timeout=30)
        except RepositoryNotFoundError as exc:
            raise DownloadError("not_found", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - network errors of all kinds
            raise DownloadError("network", str(exc)) from exc
        files = [(n, s, hf_hub_url(meta.id, n, revision=meta.sha))
                 for n, s in _selected_files(info, [(s.rfilename, s.size or 0) for s in meta.siblings or []])]
        if not files:
            raise DownloadError("not_found", f"no files in {info.repo}")
        for name, repo in info.extra_files.items():
            try:
                other = HfApi().model_info(repo, files_metadata=True, timeout=30)
            except RepositoryNotFoundError as exc:
                raise DownloadError("not_found", str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise DownloadError("network", str(exc)) from exc
            sib = next((s for s in other.siblings or [] if s.rfilename == name), None)
            if sib is None:
                raise DownloadError("not_found", f"{name} not found in {repo}")
            files = [f for f in files if f[0] != name] + [(name, sib.size or 0, hf_hub_url(other.id, name, revision=other.sha))]
        total = sum(size for _, size, _ in files)

        def have(name: str, size: int) -> int:
            final = target / name
            if final.is_file() and final.stat().st_size == size:
                return size
            part = target / (name + PARTIAL_SUFFIX)
            return part.stat().st_size if part.is_file() else 0

        done = sum(have(n, s) for n, s, _ in files)
        free = shutil.disk_usage(target).free
        if total - done + DISK_MARGIN > free:
            raise DownloadError("disk_space", f"need {(total - done) / 2**30:.1f} GB, {free / 2**30:.1f} GB free")

        session = get_session()
        headers = build_hf_headers()
        speed = TransferSpeed()
        on_progress(Progress(done, total, 0.0, ""))

        for name, size, url in files:
            final = target / name
            if final.is_file() and final.stat().st_size == size:
                continue
            final.parent.mkdir(parents=True, exist_ok=True)
            part = target / (name + PARTIAL_SUFFIX)
            others = sum(have(n, s) for n, s, _ in files if n != name)
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

    write_marker(info, {n: s for n, s, _ in files}, base)
    on_progress(Progress(total, total, 0.0, ""))
    log.info("Downloaded %s to %s", info.repo, target)
    return target
