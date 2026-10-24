"""Keyword detection: find keywords in caption text, by text and, for Chinese, by sound.

A keyword can be written as words (any language), as Chinese characters (Traditional or Simplified),
as Zhuyin (ㄊㄞˊ ㄅㄟˇ) or as Pinyin (tái běi, tai2 bei3, tai'bei; plain "taibei" when the keyword's
"Match by sound" is on, so English words like "time" or "China" aren't matched by sound by accident).
Chinese is matched by sound with tones ignored, so 台北 also finds 臺北, 抬北 or 台被 when the model
mishears a word. ``FUZZY`` adds the usual 模糊音 pairs (zh/z, n/l, in/ing, ...). The readings come from
pypinyin, loaded on first use; without it, keywords are matched as text only.
"""

from __future__ import annotations

import functools
import itertools
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)
_warned = False  # about sound matching being unavailable

# 模糊音: sounds that are often not told apart, as pairs that can be switched on.
FUZZY = ("zh/z", "ch/c", "sh/s", "n/l", "f/h", "an/ang", "en/eng", "in/ing")
# The f/h pair: hu- syllables and the f- syllables that sound like them (fa = hua, fei = hui, ...).
_HU_TO_F = {"hua": "fa", "hui": "fei", "huan": "fan", "hun": "fen", "huang": "fang", "huo": "fo", "hu": "fu"}

_HAN = re.compile(r"[㐀-鿿豈-﫿]")
_HAN_RUN = re.compile(r"[㐀-鿿豈-﫿]+")
_ZHUYIN = re.compile(r"[ㄅ-ㄯㆠ-ㆿˊˇˋ˙]")
_ZHUYIN_TONES = "ˊˇˋ˙"
_TONE_MARKED = re.compile(r"[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]")
_PINYIN_CHARS = re.compile(r"[A-Za-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜü:0-5'’\s-]+")
_LATIN = re.compile(r"[A-Za-z0-9]")
MAX_ALTERNATIVES = 8  # ways of splitting one keyword into syllables that are tried


# Characters of settled text that must follow a match found by sound before it counts in a line still
# being recognized: a polyphone is read from the characters after it (這個行 is xing, 這個行業 is hang).
SETTLED_AFTER = 2


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    keyword: str
    by_sound: bool = False


def settled(match: Match, stable_length: int) -> bool:
    """Whether a match in the settled part of a line still being recognized (``stable_length`` characters)
    can't change any more: text matches can't, sound matches once the reading of their characters is fixed."""
    return not match.by_sound or match.end <= stable_length - SETTLED_AFTER


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
def _spelled_syllables() -> frozenset[str]:
    """The syllables people type: without the vowel-less interjections (m, n, ng, hm, ...), which would
    otherwise split words like "linn" oddly."""
    return frozenset(s for s in _syllables() if re.search("[aeiouv]", s))


@functools.lru_cache(maxsize=1)
def _zhuyin_to_pinyin() -> dict[str, frozenset[str]]:
    """Each Zhuyin syllable and every Pinyin syllable written that way (ㄨㄥ: weng and wong)."""
    _, _, bopomofo = _pypinyin()
    table: dict[str, set[str]] = {}
    for s in sorted(_syllables()):
        z = bopomofo.to_bopomofo(s).strip(_ZHUYIN_TONES)
        if z:
            table.setdefault(z, set()).add(s)
    return {z: frozenset(s) for z, s in table.items()}


def _sounds(text: str) -> list[str]:
    """The syllable of each character of ``text`` ("" for anything but Chinese characters)."""
    out = [""] * len(text)
    for run in _HAN_RUN.finditer(text):
        out[run.start():run.end()] = _read(run.group())
    return out


def _splits(text: str, units, pinyin: bool) -> list[tuple[str, ...]]:
    """The ways ``text`` splits into ``units`` (Pinyin syllables or Zhuyin), at most MAX_ALTERNATIVES.

    For Pinyin, a syllable after the first can't start with a, o or e: that spelling needs an apostrophe
    (xi'an), so "henan" is he+nan, never hen+an. Memoized by position, so it's linear in practice.
    """
    @functools.lru_cache(maxsize=None)
    def rest(i: int) -> tuple[tuple[str, ...], ...]:
        if i == len(text):
            return ((),)
        out: list[tuple[str, ...]] = []
        for n in range(min(6, len(text) - i), 0, -1):
            unit = text[i:i + n]
            if unit not in units or (pinyin and i > 0 and unit[0] in "aoe"):
                continue
            for tail in rest(i + n):
                out.append((unit, *tail))
                if len(out) >= MAX_ALTERNATIVES:
                    return tuple(out)
        return tuple(out)

    return list(rest(0)) if text else [()]


def _combine(pieces: list[list[tuple[str, ...]]]) -> list[tuple[str, ...]]:
    """Every way of joining the pieces' splits, at most MAX_ALTERNATIVES."""
    return [tuple(itertools.chain(*parts)) for parts in itertools.islice(itertools.product(*pieces), MAX_ALTERNATIVES)]


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

Sound = list[frozenset[str]]  # the possible syllables, one set per character


def _is_marked_pinyin(text: str) -> bool:
    return bool(_TONE_MARKED.search(text) or re.search(r"[0-5'’]", text))


def latin_could_be_pinyin(keyword: str) -> bool:
    """A keyword in plain Latin letters that spells Pinyin syllables ("taibei", but also "China"): the
    Keywords page offers a "Match by sound" switch for it."""
    text = keyword.strip()
    if not text or not re.fullmatch(r"[A-Za-z\s-]+", text):
        return False
    try:
        return _latin_sound(text) is not None
    except Exception:  # noqa: BLE001 - pypinyin missing
        return False


def _latin_sound(text: str) -> list[Sound] | None:
    pieces = []
    for piece in re.split(r"[\s'’0-5-]+", text):
        if piece:
            splits = _splits(_plain(piece), _spelled_syllables(), pinyin=True)
            if not splits:
                return None
            pieces.append(splits)
    alternatives = _combine(pieces) if pieces else []
    return [[frozenset([s]) for s in alt] for alt in alternatives] or None


def sound_of(keyword: str, latin_sound: bool = False) -> list[Sound] | None:
    """How a keyword sounds: the ways it can be read (one when it's unambiguous), each as the possible
    toneless syllables of every character. None when it's matched as text only: not Chinese, Zhuyin or
    Pinyin, or plain Latin letters while ``latin_sound`` (the keyword's "Match by sound") is off."""
    text = keyword.strip()
    if not text:
        return None
    if _HAN.search(text):
        chars = "".join(text.split())
        if not _HAN_RUN.fullmatch(chars):
            return None  # mixed with other letters: matched as text only
        syllables = _read(chars)
        return [[frozenset([s]) for s in syllables]] if all(s in _syllables() for s in syllables) else None
    if _ZHUYIN.search(text):
        table = _zhuyin_to_pinyin()
        pieces = []
        for piece in re.split(rf"[\s{_ZHUYIN_TONES}]+", text):
            if piece:
                splits = _splits(piece, table, pinyin=False)
                if not splits:
                    return None
                # Fewest syllables: ㄨㄥ is weng, not ㄨ+ㄥ (wu eng), unless typed apart.
                fewest = min(len(x) for x in splits)
                pieces.append([x for x in splits if len(x) == fewest])
        return [[table[z] for z in alt] for alt in _combine(pieces)] or None
    if not _PINYIN_CHARS.fullmatch(text):
        return None
    if not (latin_sound or _is_marked_pinyin(text)):
        return None
    return _latin_sound(text)


def describe(keyword: str, latin_sound: bool = False) -> str:
    """How a keyword is matched, for the Keywords page: its sound in Pinyin, or "" for text only."""
    sound = sound_of(keyword, latin_sound)
    if not sound:
        return ""
    return " / ".join(" ".join("/".join(sorted(s)) for s in alt) for alt in sound)


def merge_spans(matches: list[Match]) -> list[tuple[int, int]]:
    """The text to highlight: the matches' spans with overlapping ones joined."""
    spans: list[tuple[int, int]] = []
    for m in sorted(matches, key=lambda m: (m.start, -m.end)):
        if spans and m.start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], m.end))
        else:
            spans.append((m.start, m.end))
    return spans


def utf16_spans(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``spans`` (Python string indexes) as positions in UTF-16 units, as Qt's text documents count them:
    an emoji or a rare character (𠀀) before a match takes two."""
    if text.isascii() or all(ord(c) < 0x10000 for c in text):
        return spans
    position = [0]
    for c in text:
        position.append(position[-1] + (2 if ord(c) >= 0x10000 else 1))
    return [(position[a], position[b]) for a, b in spans]


class KeywordMatcher:
    """Finds keywords in caption lines. ``keywords``: texts, or (text, latin_sound) pairs."""

    def __init__(self, keywords, fuzzy: frozenset[str] | set[str] = frozenset(), sound: bool = True):
        """``sound``: also match by sound (False: text only, e.g. before pypinyin has loaded)."""
        self.fuzzy = frozenset(fuzzy)
        self._text: list[tuple[str, re.Pattern]] = []
        self._sound: list[tuple[str, list[Sound]]] = []
        seen = set()
        for item in keywords:
            text, latin_sound = (item, False) if isinstance(item, str) else item
            keyword = text.strip()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            words = r"\s+".join(re.escape(w) for w in keyword.split())
            # Whole words for Latin letters ("cat" isn't in "category"); anywhere for other scripts.
            if _LATIN.match(keyword[0]):
                words = r"(?<![A-Za-z0-9])" + words
            if _LATIN.match(keyword[-1]):
                words += r"(?![A-Za-z0-9])"
            self._text.append((keyword, re.compile(words, re.IGNORECASE)))
            if not sound:
                continue
            try:
                alternatives = sound_of(keyword, latin_sound)
            except Exception:  # noqa: BLE001 - e.g. pypinyin missing: this keyword is matched as text only
                global _warned
                if not _warned:
                    _warned = True
                    log.warning("Keywords can't be matched by sound; matching text only", exc_info=True)
                continue
            if alternatives:
                self._sound.append((keyword, [[frozenset(normalize(s, self.fuzzy) for s in syl) for syl in alt]
                                              for alt in alternatives]))

    def __bool__(self) -> bool:
        return bool(self._text)

    def find(self, text: str) -> list[Match]:
        """Every keyword in ``text``, in order. Overlapping matches are all kept (merge_spans joins them
        for highlighting), so two keywords in 台北京 are both reported."""
        found = {Match(m.start(), m.end(), k) for k, pattern in self._text for m in pattern.finditer(text)}
        if self._sound and _HAN.search(text):
            sounds = [normalize(s, self.fuzzy) if s else "" for s in _sounds(text)]
            for keyword, alternatives in self._sound:
                for want in alternatives:
                    n = len(want)
                    for i in range(len(text) - n + 1):
                        if all(sounds[i + j] in want[j] for j in range(n)) and Match(i, i + n, keyword) not in found:
                            found.add(Match(i, i + n, keyword, True))  # (unless it's there as text too)
        return sorted(found, key=lambda m: (m.start, -m.end, m.keyword))
