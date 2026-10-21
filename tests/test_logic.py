"""Unit tests for logic that doesn't need audio hardware, a GPU or a model.

    python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.asr.backend import Transcript  # noqa: E402
from app.asr.filters import HallucinationFilter, collapse_repeats, is_stock_phrase  # noqa: E402
from app.asr.streaming import PartialStabilizer, tokenize  # noqa: E402
from app.audio.buffer import AudioBuffer  # noqa: E402
from app.audio.mixer import Limiter, TimedQueue  # noqa: E402
from app.core.history import HistoryLine, HistoryStore  # noqa: E402
from app.models.catalog import get_model, load_catalog  # noqa: E402
from app.models.downloader import _selected_files  # noqa: E402
from app.models.storage import is_downloaded, model_path, verify  # noqa: E402


class FilterTests(unittest.TestCase):
    def test_stock_phrases(self):
        for text in ("Thank you for watching!", "謝謝觀看", "字幕由Amara.org社群提供", "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目"):
            self.assertTrue(is_stock_phrase(text), text)
        for text in ("The quick brown fox", "今天天氣很好", "Visit example dot com"):
            self.assertFalse(is_stock_phrase(text), text)

    def test_collapse_repeats(self):
        self.assertEqual(collapse_repeats("哈哈哈哈哈哈"), "哈哈")
        self.assertEqual(collapse_repeats("go go go go home"), "go go home")

    def test_repeated_final_dropped(self):
        f = HallucinationFilter()
        tr = Transcript(text="Hello there", avg_logprob=-0.2)
        self.assertEqual(f.clean(tr, 2.0, final=True), "Hello there")
        self.assertEqual(f.clean(tr, 2.0, final=True), "")

    def test_no_speech_dropped(self):
        f = HallucinationFilter()
        self.assertEqual(f.clean(Transcript(text="blah", no_speech_prob=0.9, avg_logprob=-1.5), 1.0, final=True), "")


class StabilizerTests(unittest.TestCase):
    def test_tokenize_cjk_and_words(self):
        self.assertEqual([t.strip() for t in tokenize("Hello 世界 ok")], ["Hello", "世", "界", "ok"])

    def test_local_agreement_commits_common_prefix(self):
        s = PartialStabilizer()
        stable, _ = s.update("the quick")
        self.assertEqual(stable, "")
        stable, tail = s.update("the quick brown")
        self.assertEqual(stable, "the quick")
        self.assertEqual(tail.strip(), "brown")
        # A revision of committed words keeps the committed text.
        stable, _ = s.update("a quick brown fox")
        self.assertTrue(stable.startswith("the quick"))


class AudioTests(unittest.TestCase):
    def test_buffer_pull_and_drop(self):
        b = AudioBuffer(max_samples=10)
        b.push(np.arange(6, dtype=np.float32))
        b.push(np.arange(6, 12, dtype=np.float32))
        self.assertEqual(len(b), 10)  # two oldest samples dropped
        np.testing.assert_array_equal(b.pull(3), [2, 3, 4])
        self.assertEqual(len(b.pull()), 7)

    def test_limiter_prevents_clipping(self):
        x = np.concatenate([np.full(1600, 0.2), np.full(1600, 1.8)]).astype(np.float32)
        y = Limiter().process(x)
        self.assertLessEqual(float(np.max(np.abs(y))), 1.0)
        self.assertAlmostEqual(float(y[100]), 0.2, places=3)

    def test_timed_queue_drops_oldest(self):
        q = TimedQueue(max_seconds=1.0, rate=10)
        for i in range(5):
            q.push(np.full(4, i, np.float32), time.monotonic())
        audio, _ = q.pop_all(timeout=0)
        self.assertLessEqual(audio.size, 10)
        self.assertEqual(float(audio[-1]), 4.0)


class HistoryTests(unittest.TestCase):
    def test_srt_and_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            store = HistoryStore(Path(d))
            s = store.new_session("Whisper", "System audio")
            s.lines += [HistoryLine(0.5, 2.25, "first"), HistoryLine(3661.0, 3662.0, "second")]
            store.save(s)
            loaded = store.load(s.id)
            self.assertEqual([ln.text for ln in loaded.lines], ["first", "second"])
            srt = loaded.to_srt()
            self.assertIn("00:00:00,500 --> 00:00:02,250", srt)
            self.assertIn("01:01:01,000 --> 01:01:02,000", srt)
            store.clear()
            self.assertEqual(store.list_sessions(), [])


class CatalogTests(unittest.TestCase):
    def test_catalog_entries(self):
        ids = [m.id for m in load_catalog()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("whisper-large-v3-turbo", ids)
        self.assertEqual(get_model("taiwan-tongues-asr-ce").languages, ("zh", "en"))

    def test_every_model_runs_on_faster_whisper(self):
        # PyTorch/transformers were dropped; Breeze ASR 25 uses a CTranslate2 conversion.
        self.assertEqual({m.backend for m in load_catalog()}, {"faster-whisper"})
        self.assertEqual(get_model("breeze-asr-25").repo, "SoybeanMilk/faster-whisper-Breeze-ASR-25")

    def test_default_ignore_patterns(self):
        files = [("model.bin", 1), ("README.md", 1), (".gitattributes", 1), ("tokenizer.json", 1)]
        picked = {n for n, _ in _selected_files(get_model("whisper-tiny"), files)}
        self.assertEqual(picked, {"model.bin", "tokenizer.json"})

    def test_model_counts_as_downloaded_with_its_weights(self):
        info = get_model("breeze-asr-25")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            d = model_path(info, base)
            d.mkdir()
            (d / "config.json").write_text("{}")
            self.assertFalse(is_downloaded(info, base))
            self.assertEqual(verify(info, base), "missing file model.bin")
            (d / "model.bin").write_bytes(b"x")
            self.assertTrue(is_downloaded(info, base))
            self.assertIsNone(verify(info, base))

if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    unittest.main()
