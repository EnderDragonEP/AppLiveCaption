"""Keyword detection in the running captions: highlighting, alerts and the list of recent matches.

The matching itself is app/core/keywords.py. A keyword is reported once per caption line: as soon as it is
in the part of the line still being recognized that won't change any more (the stable part, and for a match
by sound also the characters its reading depends on), or else when the line is finished.
"""

from __future__ import annotations

import collections
import logging
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor

from ..config import cfg
from ..core.keywords import KeywordMatcher, Match, merge_spans, settled
from .caption_model import CaptionModel

log = logging.getLogger(__name__)

SOUND_GAP = 1.5  # seconds between alert sounds, however many keywords are heard
NOTIFY_GAP = 10.0  # seconds between Windows notifications (matches in between still reach Recent matches)
CACHE_SIZE = 300  # caption lines whose matches are kept (the live captions and the overlay redraw often)


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
        self._cache: collections.OrderedDict[str, list[Match]] = collections.OrderedDict()
        self._alerted: set[str] = set()  # keywords already reported for the line being recognized
        self._finals_seen = captions.final_count
        self._last_sound = 0.0
        self._last_notify = -NOTIFY_GAP
        # Matching by sound loads pypinyin (about half a second): match text only until the window is up.
        self._sound_ready = False
        for item in (cfg.keywords, cfg.keywordFuzzy):
            item.valueChanged.connect(lambda _v: self.rebuild())
        for item in (cfg.keywordHighlight, cfg.keywordColor):
            item.valueChanged.connect(lambda _v: self._restyle())
        captions.changed.connect(self._on_captions)
        self.rebuild()
        QTimer.singleShot(0, self._load_sound)

    # -- matching ------------------------------------------------------------------------------
    def _load_sound(self) -> None:
        self._sound_ready = True
        self.rebuild()

    def rebuild(self) -> None:
        items = [(k["text"], k.get("sound", False)) for k in cfg.get(cfg.keywords) if k.get("enabled", True)]
        self.matcher = KeywordMatcher(items, set(cfg.get(cfg.keywordFuzzy)), sound=self._sound_ready)
        self._cache.clear()
        self._restyle()

    def _restyle(self) -> None:
        """Only how matches are shown changed (or the keywords): redraw the captions."""
        on = bool(self.matcher) and cfg.get(cfg.keywordHighlight)
        self.captions.highlighter = self.spans if on else None
        self.captions.highlight_color = QColor(cfg.get(cfg.keywordColor))
        self.changed.emit()
        self.captions.changed.emit()

    def matches(self, text: str) -> list[Match]:
        if not self.matcher or not text:
            return []
        found = self._cache.get(text)
        if found is None:
            found = self._cache[text] = self.matcher.find(text)
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)  # the least recently used line
        else:
            self._cache.move_to_end(text)
        return found

    def spans(self, text: str) -> list[tuple[int, int]]:
        """What to highlight in a caption line (cached: the captions redraw often)."""
        return merge_spans(self.matches(text))

    def spans_uncached(self, text: str) -> list[tuple[int, int]]:
        """The same for History, whose many lines would push the live captions' lines out of the cache."""
        return merge_spans(self.matcher.find(text)) if self.matcher and text else []

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
            stable = len(c.partial_stable)
            keywords = [m.keyword for m in self.matches(c.partial_stable)
                        if settled(m, stable) and m.keyword not in self._alerted]
            self._alert(list(dict.fromkeys(keywords)), c.partial)
        elif not c.partial:  # cleared (captioning restarted): a new line
            self._alerted.clear()

    def _alert(self, keywords: list[str], line: str) -> None:
        if not keywords:
            return
        self._alerted.update(keywords)
        self.heard.emit(", ".join(keywords), line)  # (one alert for a line with several keywords)

    def may_notify(self) -> bool:
        """Whether a Windows notification may be shown now (at most one every NOTIFY_GAP seconds)."""
        now = time.monotonic()
        if now - self._last_notify < NOTIFY_GAP:
            return False
        self._last_notify = now
        return True

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
        """Add a keyword; False if it's empty or listed already. "Match by sound" starts off."""
        text = " ".join(text.split())
        current = self.keywords()
        if not text or any(k["text"].casefold() == text.casefold() for k in current):
            return False
        cfg.set(cfg.keywords, [*current, {"text": text, "enabled": True, "sound": False}])
        return True

    def remove(self, text: str) -> None:
        cfg.set(cfg.keywords, [k for k in self.keywords() if k["text"] != text])

    def set_enabled(self, text: str, enabled: bool) -> None:
        cfg.set(cfg.keywords, [{**k, "enabled": enabled} if k["text"] == text else k for k in self.keywords()])

    def set_sound(self, text: str, sound: bool) -> None:
        cfg.set(cfg.keywords, [{**k, "sound": sound} if k["text"] == text else k for k in self.keywords()])
