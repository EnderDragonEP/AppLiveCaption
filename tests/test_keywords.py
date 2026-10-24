"""Keyword detection: text matching, and Chinese matching by sound (Pinyin, Zhuyin, 模糊音)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import keywords  # noqa: E402
from app.core.keywords import (  # noqa: E402
    KeywordMatcher,
    describe,
    latin_could_be_pinyin,
    merge_spans,
    normalize,
    settled,
    sound_of,
    utf16_spans,
)


def found(keywords, text, fuzzy=()):
    return [text[m.start:m.end] for m in KeywordMatcher(keywords, set(fuzzy)).find(text)]


class TextTests(unittest.TestCase):
    def test_whole_words_any_case(self):
        self.assertEqual(found(["cat"], "The Cat sat in a category."), ["Cat"])
        self.assertEqual(found(["new york"], "Flights to New  York today"), ["New  York"])
        self.assertEqual(found(["GPT-4"], "We use gpt-4 here"), ["gpt-4"])

    def test_overlaps_are_all_reported_and_merged_for_highlighting(self):
        matches = KeywordMatcher(["new", "new york"]).find("new york")
        self.assertEqual({m.keyword for m in matches}, {"new", "new york"})
        self.assertEqual(merge_spans(matches), [(0, 8)])
        self.assertEqual({m.keyword for m in KeywordMatcher(["台北", "北京"]).find("台北京")}, {"台北", "北京"})

    def test_no_keywords(self):
        self.assertFalse(KeywordMatcher(["", "  "]))
        self.assertEqual(found([], "anything"), [])

    def test_text_matching_works_without_pypinyin(self):
        with mock.patch.object(keywords, "_read", side_effect=ImportError("no pypinyin")), \
                mock.patch.object(keywords, "_pypinyin", side_effect=ImportError("no pypinyin")):
            matcher = KeywordMatcher(["Ender", "台北", ("taibei", True)])
            self.assertEqual([m.keyword for m in matcher.find("Hi Ender, 台北")], ["Ender", "台北"])


class SoundTests(unittest.TestCase):
    def test_same_sound_any_tone_either_script(self):
        self.assertEqual(found(["台北"], "我住在臺北，不是抬北，也不是台被。"), ["臺北", "抬北", "台被"])
        self.assertEqual(found(["台北"], "台中"), [])

    def test_polyphones_read_in_context(self):  # 行 is hang in 銀行, xing in 行人
        self.assertEqual(found(["航"], "去銀行，看行人"), ["行"])
        self.assertEqual(describe("台北"), "tai bei")  # not also the rare readings si, yi

    def test_zhuyin_and_pinyin_keywords(self):
        text = "今天去台北"
        for keyword in ("ㄊㄞˊ ㄅㄟˇ", "ㄊㄞˊㄅㄟˇ", "ㄊㄞㄅㄟ", ("taibei", True), ("tai bei", True), "tái běi",
                        "tai2 bei3", "Tai'bei"):
            self.assertEqual(found([keyword], text), ["台北"], keyword)

    def test_zhuyin_with_several_pinyin_spellings_is_stable(self):
        self.assertEqual(found(["ㄨㄥ"], "老翁"), ["翁"])
        keywords._zhuyin_to_pinyin.cache_clear()
        self.assertEqual(found(["ㄨㄥ"], "老翁"), ["翁"])

    def test_pinyin_spelling_rule(self):  # a syllable after the first can't start with a, o or e
        for keyword, text in (("Henan", "河南"), ("Hunan", "湖南"), ("Jinan", "济南"), ("xi'an", "西安")):
            self.assertEqual(found([(keyword, True)], text), [text], keyword)
        self.assertEqual(found([("xian", True)], "西安"), [])  # xian is one syllable (先)

    def test_long_input_is_fast(self):
        start = time.perf_counter()
        self.assertIsNone(sound_of("na" * 20 + "q", latin_sound=True))
        describe("na" * 20 + "x", latin_sound=True)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_plain_letters_need_match_by_sound(self):
        self.assertTrue(latin_could_be_pinyin("China"))
        self.assertFalse(latin_could_be_pinyin("hello"))
        self.assertIsNone(sound_of("China"))                    # English unless switched on
        self.assertEqual(found(["time"], "我提么说"), [])
        self.assertEqual(found([("China", True)], "我吃那个"), ["吃那"])
        self.assertIsNotNone(sound_of("hé"))                    # a tone mark makes it Pinyin
        self.assertEqual(found(["he"], "他和她"), [])

    def test_fuzzy_pairs(self):
        self.assertEqual(found(["四"], "是"), [])
        self.assertEqual(found(["四"], "是", {"sh/s"}), ["是"])
        self.assertEqual(found(["牛奶"], "流來", {"n/l"}), ["流來"])
        self.assertEqual(found(["發"], "花", {"f/h"}), ["花"])
        self.assertEqual(found(["新"], "星", {"in/ing"}), ["星"])
        self.assertEqual(normalize("zhang", frozenset({"zh/z", "an/ang"})), "zan")
        self.assertEqual(normalize("ang", frozenset({"an/ang"})), "an")

    def test_sound_matches_wait_for_the_following_characters(self):
        matcher = KeywordMatcher(["星", "這個"])
        stable = "這個行"  # 行 is read xing here, but hang once 業 follows
        by_keyword = {m.keyword: m for m in matcher.find(stable)}
        self.assertFalse(settled(by_keyword["星"], len(stable)))  # by sound, at the end: wait
        self.assertTrue(settled(by_keyword["這個"], len(stable)))  # a text match is settled at once
        self.assertEqual([m.keyword for m in matcher.find("這個行業")], ["這個"])
        self.assertTrue(settled(KeywordMatcher(["台北"]).find("抬北去了")[0], 4))

    def test_utf16_positions_for_qt(self):
        text = "🎵 台北 and 𠀀台北"
        spans = merge_spans(KeywordMatcher(["台北"]).find(text))
        self.assertEqual(spans, [(2, 4), (10, 12)])
        self.assertEqual(utf16_spans(text, spans), [(3, 5), (12, 14)])
        self.assertEqual(utf16_spans("台北", [(0, 2)]), [(0, 2)])

    def test_describe(self):
        self.assertEqual(describe("ㄊㄞˊ ㄅㄟˇ"), "tai bei")
        self.assertEqual(describe("Ender"), "")
        self.assertEqual(describe("ㄨㄥ"), "weng/wong")


if __name__ == "__main__":
    unittest.main()
