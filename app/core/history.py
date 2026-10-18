"""Caption history: one JSON file per captioning session."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..paths import history_dir

log = logging.getLogger(__name__)


@dataclass
class HistoryLine:
    start: float  # seconds since the session started
    end: float
    text: str
    wall: float = 0.0  # Unix time the line was captioned


@dataclass
class HistorySession:
    id: str
    started: float  # Unix time
    model: str = ""
    source: str = ""
    lines: list[HistoryLine] = field(default_factory=list)

    @property
    def title(self) -> str:
        return datetime.fromtimestamp(self.started).strftime("%Y-%m-%d %H:%M")

    @property
    def duration(self) -> float:
        return self.lines[-1].end if self.lines else 0.0

    def to_txt(self) -> str:
        out = []
        for ln in self.lines:
            stamp = datetime.fromtimestamp(ln.wall).strftime("%H:%M:%S") if ln.wall else _clock(ln.start)
            out.append(f"[{stamp}] {ln.text}")
        return "\n".join(out) + ("\n" if out else "")

    def to_srt(self) -> str:
        blocks = []
        for i, ln in enumerate(self.lines, 1):
            end = max(ln.end, ln.start + 0.5)
            blocks.append(f"{i}\n{_srt_time(ln.start)} --> {_srt_time(end)}\n{ln.text}\n")
        return "\n".join(blocks)


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class HistoryStore:
    def __init__(self, folder: Path | None = None):
        self.folder = folder or history_dir()
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        return self.folder / f"{session_id}.json"

    def new_session(self, model: str, source: str) -> HistorySession:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return HistorySession(id=f"{stamp}-{uuid.uuid4().hex[:6]}", started=time.time(), model=model, source=source)

    def save(self, session: HistorySession) -> None:
        if not session.lines:
            return
        data = asdict(session)
        tmp = self._path(session.id).with_suffix(".tmp")
        with self._lock:
            try:
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self._path(session.id))
            except OSError:
                log.exception("Could not save history session %s", session.id)

    def load(self, session_id: str) -> HistorySession | None:
        try:
            data = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        lines = [HistoryLine(**ln) for ln in data.pop("lines", [])]
        return HistorySession(lines=lines, **data)

    def list_sessions(self) -> list[HistorySession]:
        sessions = []
        for p in sorted(self.folder.glob("*.json"), reverse=True):
            s = self.load(p.stem)
            if s is not None:
                sessions.append(s)
        return sessions

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._path(session_id).unlink(missing_ok=True)

    def clear(self) -> None:
        with self._lock:
            for p in self.folder.glob("*.json"):
                p.unlink(missing_ok=True)
