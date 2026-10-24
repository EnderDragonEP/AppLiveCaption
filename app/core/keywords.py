"""Keyword detection: find keywords in caption text, by text and, for Chinese, by sound.

A keyword can be written as words (any language), as Chinese characters (Traditional or Simplified),
as Zhuyin (ㄊㄞˊ ㄅㄟˇ) or as Pinyin (taibei, tái běi, tai2 bei3). Chinese is matched by sound with
tones ignored, so 台北 also finds 臺北, 抬北 or 台被 when the model mishears a word. ``FUZZY`` adds the
usual 模糊音 pairs (zh/z, n/l, in/ing, ...). The readings come from pypinyin, loaded on first use.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

# 模糊音: sounds that are often not told apart, as pairs that can be switched on.
FUZZY = ("zh/z", "ch/c", "sh/s", "n/l", "f/h", "an/ang", "en/eng", "in/ing")
# The f/h pair: hu- syllables and the f- syllables that sound like them (fa = hua, fei = hui, ...).
_HU_TO_F = {"hua": "fa", "hui": "fei", "huan": "fan", "hun": "fen", "huang": "fang", "huo": "fo", "hu": "fu"}

_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_HAN_RUN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
_ZHUYIN = re.compile(r"[ㄅ-ㄯㆠ-ㆿˊˇˋ˙]")
_ZHUYIN_TONES = "ˊˇˋ˙"
_TONE_MARKED = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü]")
_LATIN = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    keyword: str


# -- pypinyin ----------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _pypinyin():
    from pypinyin.contrib.tone_convert import to_normal
    from pypinyin.pinyin_dict import pinyin_dict
    from pypinyin.style.bopomofo import BopomofoConverter

    return pinyin_dict, to_normal, BopomofoConverter()


@functools.lru_cache(maxsize=1)
def _simplifier():
    try:
        import opencc

        return opencc.OpenCC("t2s")
    except Exception:  # noqa: BLE001 - without it, Traditional words may be read less well
        return None


def _to_simplified(text: str) -> str:
    cc = _simplifier()
    return cc.convert(text) if cc else text


@functools.lru_cache(maxsize=4096)
def _read(han: str) -> tuple[str, ...]:
    """How a run of Chinese characters is read, one toneless syllable per character. Polyphones are read
    the way the words around them say (銀行: hang, 行人: xing), not as every reading they could have."""
    from pypinyin import Style, lazy_pinyin

    # pypinyin knows its words in Simplified Chinese (銀行 would be read yin xing), so read it in that.
    simplified = _to_simplified(han)
    syllables = lazy_pinyin(simplified if len(simplified) == len(han) else han, style=Style.NORMAL,
                            errors=lambda chars: list(chars))
    if len(syllables) != len(han):  # (shouldn't happen: one per character)
        syllables = [lazy_pinyin(c, style=Style.NORMAL)[0] for c in han]
    return tuple(_plain(x) for x in syllables)


def _plain(syllable: str) -> str:
    """A Pinyin syllable without tone, in lower case, with ü written as v."""
    _, to_normal, _ = _pypinyin()
    return to_normal(syllable.lower()).replace("ü", "v").replace("u:", "v")


@functools.lru_cache(maxsize=1)
def _syllables() -> frozenset[str]:
    """Every toneless Pinyin syllable (about 400)."""
    pinyin_dict, _, _ = _pypinyin()
    return frozenset(_plain(p) for readings in pinyin_dict.values() for p in readings.split(","))


@functools.lru_cache(maxsize=1)
def _zhuyin_to_pinyin() -> dict[str, str]:
    _, _, bopomofo = _pypinyin()
    table = {}
    for s in _syllables():
        z = bopomofo.to_bopomofo(s).strip(_ZHUYIN_TONES)
        if z:
            table.setdefault(z, s)
    return table


def _sounds(text: str) -> list[str]:
    """The syllable of each character of ``text`` ("" for anything but Chinese characters)."""
    out = [""] * len(text)
    for run in _HAN_RUN.finditer(text):
        out[run.start():run.end()] = _read(run.group())
    return out


def _segment(text: str, units) -> list[str] | None:
    """``text`` split into units (Pinyin syllables or Zhuyin), longest first; None if it can't be."""
    if not text:
        return []
    for n in range(min(6, len(text)), 0, -1):
        if text[:n] in units:
            rest = _segment(text[n:], units)
            if rest is not None:
                return [text[:n], *rest]
    return None


# -- fuzzy sounds --------------------------------------------------------------------------------

def normalize(syllable: str, fuzzy: frozenset[str]) -> str:
    """``syllable`` with the switched-on 模糊音 pairs folded, so both sides of a pair compare equal."""
    s = syllable
    for pair, long_initial in (("zh/z", "zh"), ("ch/c", "ch"), ("sh/s", "sh")):
        if pair in fuzzy and s.startswith(long_initial):
            s = long_initial[0] + s[2:]
    if "n/l" in fuzzy and s.startswith("n") and s not in ("n", "ng"):
        s = "l" + s[1:]
    if "f/h" in fuzzy and s in _HU_TO_F:
        s = _HU_TO_F[s]
    for pair, long_final in (("an/ang", "ang"), ("en/eng", "eng"), ("in/ing", "ing")):
        if pair in fuzzy and s.endswith(long_final):
            s = s[:-1]
    return s


# -- keywords ------------------------------------------------------------------------------------

def sound_of(keyword: str) -> list[frozenset[str]] | None:
    """How a keyword sounds, as the possible toneless readings of each syllable, or None when it isn't
    Chinese, Zhuyin or Pinyin. Plain Latin letters count as Pinyin only when they split into two or more
    syllables (taibei), so English words like "man" or "he" aren't matched by sound."""
    text = keyword.strip()
    if not text:
        return None
    if _HAN.search(text):
        chars = "".join(text.split())
        if not _HAN_RUN.fullmatch(chars):
            return None  # mixed with other letters: matched as text only
        syllables = _read(chars)
        return [frozenset([s]) for s in syllables] if all(s in _syllables() for s in syllables) else None
    if _ZHUYIN.search(text):
        table = _zhuyin_to_pinyin()
        syllables: list[str] = []
        for piece in re.split(rf"[\s{_ZHUYIN_TONES}]+", text):
            parts = _segment(piece, table)
            if parts is None:
                return None
            syllables += [table[p] for p in parts]
        return [frozenset([s]) for s in syllables] or None
    if not re.fullmatch(r"[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü:0-5'’\s-]+", text):
        return None
    marked = bool(_TONE_MARKED.search(text) or re.search(r"[0-5'’]", text))
    syllables = []
    for piece in re.split(r"[\s'’0-5-]+", text):
        if piece:
            parts = _segment(_plain(piece), _syllables())
            if parts is None:
                return None
            syllables += parts
    if not syllables or (not marked and len(syllables) < 2):
        return None
    return [frozenset([s]) for s in syllables]


def describe(keyword: str) -> str:
    """How a keyword is matched, for the Keywords page: its sound in Pinyin, or "" for text only."""
    sound = sound_of(keyword)
    return " ".join("/".join(sorted(s)) for s in sound) if sound else ""


class KeywordMatcher:
    """Finds keywords in caption lines."""

    def __init__(self, keywords: list[str], fuzzy: frozenset[str] | set[str] = frozenset()):
        self.fuzzy = frozenset(fuzzy)
        self._text: list[tuple[str, re.Pattern]] = []
        self._sound: list[tuple[str, list[frozenset[str]]]] = []
        for keyword in dict.fromkeys(k.strip() for k in keywords if k.strip()):
            words = r"\s+".join(re.escape(w) for w in keyword.split())
            # Whole words for Latin letters ("cat" isn't in "category"); anywhere for other scripts.
            if _LATIN.match(keyword[0]):
                words = r"(?<![A-Za-z0-9])" + words
            if _LATIN.match(keyword[-1]):
                words += r"(?![A-Za-z0-9])"
            self._text.append((keyword, re.compile(words, re.IGNORECASE)))
            sound = sound_of(keyword)
            if sound:
                self._sound.append((keyword, [frozenset(normalize(s, self.fuzzy) for s in syl) for syl in sound]))

    def __bool__(self) -> bool:
        return bool(self._text)

    def find(self, text: str) -> list[Match]:
        """The keywords in ``text``, in order, overlapping matches merged (the first keyword names it)."""
        found = [Match(m.start(), m.end(), k) for k, pattern in self._text for m in pattern.finditer(text)]
        if self._sound and _HAN.search(text):
            sounds = [normalize(s, self.fuzzy) if s else "" for s in _sounds(text)]
            for keyword, want in self._sound:
                n = len(want)
                for i in range(len(text) - n + 1):
                    if all(sounds[i + j] in want[j] for j in range(n)):
                        found.append(Match(i, i + n, keyword))
        found.sort(key=lambda m: (m.start, -m.end))
        merged: list[Match] = []
        for m in found:
            if merged and m.start < merged[-1].end:
                last = merged[-1]
                if m.end > last.end:
                    merged[-1] = Match(last.start, m.end, last.keyword)
            else:
                merged.append(m)
        return merged
