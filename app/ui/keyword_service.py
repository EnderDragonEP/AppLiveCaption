"""Keyword detection in the running captions: highlighting, alerts and the list of recent matches.

The matching itself is app/core/keywords.py. A keyword is reported once per caption line: as soon as it is
in the part of the line still being recognized that won't change any more (the stable part), or else when
the line is finished.
"""

from __future__ import annotations

import collections
import logging
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from ..config import cfg
from ..core.keywords import KeywordMatcher, Match
from .caption_model import CaptionModel

log = logging.getLogger(__name__)

SOUND_GAP = 1.5  # seconds between alert sounds, however many keywords are heard


@dataclass(frozen=True)
class Hit:
    when: float  # time.time()
    keyword: str
    line: str


class KeywordService(QObject):
    heard = Signal(str, str)  # the keywords heard (comma-separated), the caption line so far
    recentChanged = Signal()
    changed = Signal()  # the keywords or how they're shown changed

    def __init__(self, captions: CaptionModel, parent=None):
        super().__init__(parent)
        self.captions = captions
        self.recent: collections.deque[Hit] = collections.deque(maxlen=200)
        self.matcher = KeywordMatcher([])
        self._cache: dict[str, list[Match]] = {}
        self._alerted: set[str] = set()  # keywords already reported for the line being recognized
        self._finals_seen = captions.final_count
        self._last_sound = 0.0
        for item in (cfg.keywords, cfg.keywordFuzzy, cfg.keywordHighlight, cfg.keywordColor):
            item.valueChanged.connect(lambda _v: self.rebuild())
        captions.changed.connect(self._on_captions)
        self.rebuild()

    # -- matching ------------------------------------------------------------------------------
    def rebuild(self) -> None:
        texts = [k["text"] for k in cfg.get(cfg.keywords) if k.get("enabled", True)]
        try:
            self.matcher = KeywordMatcher(texts, set(cfg.get(cfg.keywordFuzzy)))
        except Exception:  # noqa: BLE001 - e.g. pypinyin missing: text matching still works
            log.exception("keyword sound matching unavailable")
            self.matcher = KeywordMatcher([])
        self._cache.clear()
        on = bool(self.matcher) and cfg.get(cfg.keywordHighlight)
        self.captions.highlighter = self.spans if on else None
        self.captions.highlight_color = QColor(cfg.get(cfg.keywordColor))
        self.changed.emit()
        self.captions.changed.emit()  # redraw with the new marks

    def matches(self, text: str) -> list[Match]:
        if not self.matcher or not text:
            return []
        found = self._cache.get(text)
        if found is None:
            if len(self._cache) > 500:
                self._cache.clear()
            found = self._cache[text] = self.matcher.find(text)
        return found

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [(m.start, m.end) for m in self.matches(text)]

    # -- alerts ----------------------------------------------------------------------------------
    def _on_captions(self) -> None:
        c = self.captions
        if c.final_count != self._finals_seen:  # a line was finished
            new = min(c.final_count - self._finals_seen, len(c.finals))
            self._finals_seen = c.final_count
            for line in list(c.finals)[-new:]:
                keywords = list(dict.fromkeys(m.keyword for m in self.matches(line)))
                for keyword in keywords:
                    self.recent.appendleft(Hit(time.time(), keyword, line))
                self._alert([k for k in keywords if k not in self._alerted], line)
                self._alerted.clear()
            if new:
                self.recentChanged.emit()
        elif c.partial_stable:  # only what won't change any more
            keywords = [m.keyword for m in self.matches(c.partial_stable) if m.keyword not in self._alerted]
            self._alert(list(dict.fromkeys(keywords)), c.partial)
        elif not c.partial:  # cleared (captioning restarted): a new line
            self._alerted.clear()

    def _alert(self, keywords: list[str], line: str) -> None:
        if not keywords:
            return
        self._alerted.update(keywords)
        self.heard.emit(", ".join(keywords), line)  # (one alert for a line with several keywords)

    def play_sound(self, force: bool = False) -> None:
        """The alert sound (Windows' notification sound), at most every SOUND_GAP seconds."""
        now = time.monotonic()
        if sys.platform != "win32" or (not force and now - self._last_sound < SOUND_GAP):
            return
        self._last_sound = now
        import winsound

        try:
            winsound.PlaySound("Notification.Default", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except RuntimeError:
            log.debug("couldn't play the alert sound", exc_info=True)

    def clear_recent(self) -> None:
        self.recent.clear()
        self.recentChanged.emit()

    # -- the keyword list --------------------------------------------------------------------------
    @staticmethod
    def keywords() -> list[dict]:
        return [dict(k) for k in cfg.get(cfg.keywords)]

    def add(self, text: str) -> bool:
        """Add a keyword; False if it's empty or listed already."""
        text = " ".join(text.split())
        current = self.keywords()
        if not text or any(k["text"].casefold() == text.casefold() for k in current):
            return False
        cfg.set(cfg.keywords, [*current, {"text": text, "enabled": True}])
        return True

    def remove(self, text: str) -> None:
        cfg.set(cfg.keywords, [k for k in self.keywords() if k["text"] != text])

    def set_enabled(self, text: str, enabled: bool) -> None:
        cfg.set(cfg.keywords, [{**k, "enabled": enabled} if k["text"] == text else k for k in self.keywords()])
