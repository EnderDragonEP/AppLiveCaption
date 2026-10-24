"""Keyword detection: text matching, and Chinese matching by sound (Pinyin, Zhuyin, 模糊音)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.keywords import KeywordMatcher, describe, normalize, sound_of  # noqa: E402


def found(keywords, text, fuzzy=()):
    return [text[m.start:m.end] for m in KeywordMatcher(keywords, set(fuzzy)).find(text)]


class TextTests(unittest.TestCase):
    def test_whole_words_any_case(self):
        self.assertEqual(found(["cat"], "The Cat sat in a category."), ["Cat"])
        self.assertEqual(found(["new york"], "Flights to New  York today"), ["New  York"])
        self.assertEqual(found(["GPT-4"], "We use gpt-4 here"), ["gpt-4"])

    def test_overlaps_are_merged(self):
        matcher = KeywordMatcher(["new", "new york"])
        self.assertEqual([(m.start, m.end) for m in matcher.find("new york")], [(0, 8)])

    def test_no_keywords(self):
        self.assertFalse(KeywordMatcher(["", "  "]))
        self.assertEqual(found([], "anything"), [])


class SoundTests(unittest.TestCase):
    def test_same_sound_any_tone_either_script(self):
        self.assertEqual(found(["台北"], "我住在臺北，不是抬北，也不是台被。"), ["臺北", "抬北", "台被"])
        self.assertEqual(found(["台北"], "台中"), [])

    def test_polyphones_read_in_context(self):  # 行 is hang in 銀行, xing in 行人
        self.assertEqual(found(["航"], "去銀行，看行人"), ["行"])
        self.assertEqual(describe("台北"), "tai bei")  # not also the rare readings si, yi

    def test_zhuyin_and_pinyin_keywords(self):
        text = "今天去台北"
        for keyword in ("ㄊㄞˊ ㄅㄟˇ", "ㄊㄞˊㄅㄟˇ", "ㄊㄞㄅㄟ", "taibei", "tai bei", "tái běi", "tai2 bei3", "Tai'bei"):
            self.assertEqual(found([keyword], text), ["台北"], keyword)

    def test_english_words_are_not_pinyin(self):
        self.assertIsNone(sound_of("he"))      # one syllable, no tone: English
        self.assertIsNone(sound_of("hello"))   # doesn't split into syllables
        self.assertIsNotNone(sound_of("hé"))   # a tone mark makes it Pinyin
        self.assertEqual(found(["he"], "他和她"), [])

    def test_fuzzy_pairs(self):
        self.assertEqual(found(["四"], "是"), [])
        self.assertEqual(found(["四"], "是", {"sh/s"}), ["是"])
        self.assertEqual(found(["牛奶"], "流來", {"n/l"}), ["流來"])
        self.assertEqual(found(["發"], "花", {"f/h"}), ["花"])
        self.assertEqual(found(["新"], "星", {"in/ing"}), ["星"])
        self.assertEqual(normalize("zhang", frozenset({"zh/z", "an/ang"})), "zan")
        self.assertEqual(normalize("ang", frozenset({"an/ang"})), "an")

    def test_describe(self):
        self.assertEqual(describe("ㄊㄞˊ ㄅㄟˇ"), "tai bei")
        self.assertEqual(describe("Ender"), "")


if __name__ == "__main__":
    unittest.main()
