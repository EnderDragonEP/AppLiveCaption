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
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402
from app.asr.backend import Transcript  # noqa: E402
from app.asr.filters import HallucinationFilter, collapse_repeats, is_stock_phrase  # noqa: E402
from app.asr import hardware  # noqa: E402
from app.asr.hardware import GpuInfo, pick_compute_type, realtime_risk, suggest_model  # noqa: E402
from app.asr.streaming import (  # noqa: E402
    PartialStabilizer, StreamingTranscriber, _Utterance, sentence_cuts, tokenize)
from app.asr.textconv import CHINESE_OFF, CHINESE_SIMPLIFIED, CHINESE_TRADITIONAL, ChineseConverter  # noqa: E402
from app.audio.buffer import AudioBuffer  # noqa: E402
from app.audio.vad import FRAME  # noqa: E402
from app.audio.mixer import Limiter, TimedQueue  # noqa: E402
from app.core.history import HistoryLine, HistoryStore  # noqa: E402
from app.i18n import windows_chinese_script  # noqa: E402
from app.models.catalog import get_model, load_catalog  # noqa: E402
from app.models.downloader import _selected_files  # noqa: E402
from app.models.storage import is_downloaded, model_path, verify  # noqa: E402


class ChineseOutputTests(unittest.TestCase):
    def test_both_directions(self):
        self.assertEqual(ChineseConverter(CHINESE_TRADITIONAL).convert("软件和文件夹"), "軟體和資料夾")
        self.assertEqual(ChineseConverter(CHINESE_SIMPLIFIED).convert("軟體和資料夾"), "软件和文件夹")
        self.assertEqual(ChineseConverter(CHINESE_OFF).convert("软件和資料夾"), "软件和資料夾")
        self.assertEqual(ChineseConverter(CHINESE_SIMPLIFIED).convert("Hello"), "Hello")

    def test_switching_while_running(self):
        conv = ChineseConverter(CHINESE_TRADITIONAL)
        self.assertEqual(conv.convert("语言"), "語言")
        conv.output = CHINESE_SIMPLIFIED
        self.assertEqual(conv.convert("語言"), "语言")

    def test_windows_chinese_script(self):
        self.assertEqual(windows_chinese_script(["en-US", "zh-Hant-TW", "zh-TW"]), "traditional")
        self.assertEqual(windows_chinese_script(["zh-Hans-CN", "en-US"]), "simplified")
        self.assertEqual(windows_chinese_script(["en-US", "zh-Hant-HK", "zh-Hans-CN"]), "traditional")
        self.assertEqual(windows_chinese_script(["zh-Hans-SG"]), "simplified")
        self.assertIsNone(windows_chinese_script(["en-US", "ja-JP"]))


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


class FakeBackend:
    """Answers every decode with the next text, and records how it was asked."""

    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls: list[dict] = []

    def transcribe(self, audio, language, prompt, final, translate=False, timestamps=False):
        self.calls.append({"prompt": prompt, "final": final, "translate": translate})
        return Transcript(text=self.texts.pop(0), language="zh", language_probability=0.9, avg_logprob=-0.2)


class SentenceCutTests(unittest.TestCase):
    """Cutting a long utterance after a sentence (app/asr/streaming.py)."""

    SEGMENTS = [(0.0, 3.4, "今天要帶大家去逛夜市"), (3.4, 6.0, "第一站是基隆")]

    def test_cut_after_a_settled_sentence(self):
        self.assertEqual(sentence_cuts(self.SEGMENTS, "今天要帶大家去逛夜市第一站", 6.0), [3.4])
        # Punctuation and case don't matter for "settled".
        english = [(0.0, 3.0, " Welcome back to the channel."), (3.0, 6.0, " Today we")]
        self.assertEqual(sentence_cuts(english, "welcome back to the channel today", 6.0), [3.0])

    def test_no_cut_while_it_can_still_change(self):
        self.assertEqual(sentence_cuts(self.SEGMENTS, "今天要帶大家", 6.0), [])  # the sentence isn't settled yet
        self.assertEqual(sentence_cuts(self.SEGMENTS, "", 6.0), [])
        self.assertEqual(sentence_cuts(self.SEGMENTS[:1], "今天要帶大家去逛夜市", 6.0), [])  # nothing follows it
        self.assertEqual(sentence_cuts(self.SEGMENTS, "今天要帶大家去逛夜市第一站", 4.0), [])  # too little follows
        self.assertEqual(sentence_cuts([], "", 6.0), [])

    def test_short_segments_are_joined_and_later_ends_are_offered_too(self):
        segments = [(0.0, 1.2, "大家好"), (1.2, 3.0, "歡迎回來"), (3.0, 6.0, "今天要去夜市"), (6.0, 8.0, "第一站")]
        self.assertEqual(sentence_cuts(segments, "大家好歡迎回來今天要去夜市第一", 8.0), [3.0, 6.0])
        self.assertEqual(sentence_cuts(segments, "大家好歡迎回來今天", 8.0), [3.0])
        self.assertEqual(sentence_cuts(segments, "大家好", 8.0), [])  # only the too-short part is settled
        self.assertEqual(sentence_cuts(segments[:2], "大家好歡迎回來", 8.0), [])  # 1.2 s alone is too short


class SegmentBackend:
    """Partials come as two segments (a sentence, then the start of the next); finals as one text."""

    def __init__(self):
        self.finals: list[int] = []
        self.timestamps: list[bool] = []

    def transcribe(self, audio, language, prompt, final, translate=False, timestamps=False):
        if final:
            self.finals.append(audio.size)
            return Transcript(text="第一句話說完了", language="zh", language_probability=0.9, avg_logprob=-0.2)
        self.timestamps.append(timestamps)
        # Simplified, as Whisper writes it before the app's conversion to Traditional.
        return Transcript(text="第一句话说完了第二句", language="zh", language_probability=0.9, avg_logprob=-0.2,
                          segments=[(0.0, 3.0, "第一句话说完了"), (3.0, 5.0, "第二句")])


class SentenceSplitTests(unittest.TestCase):
    def transcriber(self, backend, seconds=5.0, pause_at=None, pause_frames=4, **kw):
        with mock.patch("app.asr.streaming.StreamingVAD"):
            st = StreamingTranscriber(backend, **kw)
        n = int(seconds * 16000 / FRAME)
        probs = [0.9] * n
        if pause_at is not None:
            first = int(pause_at * 16000 / FRAME)
            probs[first:first + pause_frames] = [0.1] * pause_frames
        st._utt = _Utterance(start_pos=1600, frames=[np.zeros(FRAME, np.float32)] * n, probs=probs, language="zh")
        return st, n

    def test_a_settled_sentence_becomes_a_final_and_the_rest_carries_on(self):
        backend = SegmentBackend()
        # The pause is a little after Whisper's 3.0 s; the captions are converted to Traditional Chinese, the
        # segments aren't (the cut compares Whisper's own text).
        st, n = self.transcriber(backend, pause_at=3.2)
        quiet = int(3.2 * 16000 / FRAME) + 2  # the middle of the 4-frame pause
        self.assertEqual([e.kind for e in st._partial()], ["partial"])  # seen once: not settled yet
        events = st._partial()
        self.assertEqual([(e.kind, e.text) for e in events], [("final", "第一句話說完了")])
        self.assertEqual(backend.finals, [quiet * FRAME])  # cut in the pause, not through a word
        self.assertAlmostEqual(events[0].end, (1600 + quiet * FRAME) / 16000)
        rest = st._utt
        self.assertEqual((rest.start_pos, len(rest.frames), rest.language), (1600 + quiet * FRAME, n - quiet, "zh"))
        self.assertEqual(st.prompt_history, "第一句話說完了")

    def test_no_cut_without_a_pause(self):
        backend = SegmentBackend()
        st, n = self.transcriber(backend)  # the speaker runs the two sentences together
        for _ in range(3):
            self.assertEqual([e.kind for e in st._partial()], ["partial"])
        self.assertEqual((backend.finals, len(st._utt.frames)), ([], n))
        st, _ = self.transcriber(backend, pause_at=4.2)  # a pause, but not at the sentence's end
        st._partial()
        self.assertEqual([e.kind for e in st._partial()], ["partial"])
        st, _ = self.transcriber(backend, pause_at=3.2, pause_frames=1)  # one quiet frame: a consonant
        st._partial()
        self.assertEqual([e.kind for e in st._partial()], ["partial"])

    def test_no_cut_while_recognition_struggles(self):
        backend = SegmentBackend()
        for slow in ({"_partial_cost": 1.5}, {"last_latency": 3.0}):
            st, _ = self.transcriber(backend, pause_at=3.2)
            st._partial()
            st.__dict__.update(slow)
            with mock.patch.object(st, "_decode", wraps=st._decode) as decode:
                decode.side_effect = lambda *a, **k: (st.backend.transcribe(
                    np.zeros(1), "zh", None, False, timestamps=True), slow.get("_partial_cost", 0.1))
                self.assertEqual([e.kind for e in st._partial()], ["partial"], slow)
        self.assertEqual(backend.finals, [])

    def test_a_cut_keeps_the_partial_pace(self):
        backend = SegmentBackend()
        st, _ = self.transcriber(backend, pause_at=3.2)
        st._partial()
        before = time.monotonic()
        self.assertEqual([e.kind for e in st._partial()], ["final"])
        self.assertGreaterEqual(st._last_partial_at, before)  # (no immediate extra partial after the final)

    def test_timestamps_only_once_a_cut_is_possible(self):
        backend = SegmentBackend()
        st, _ = self.transcriber(backend, seconds=3.0)
        st._partial()
        st, _ = self.transcriber(backend, seconds=3.6)
        st._partial()
        self.assertEqual(backend.timestamps, [False, True])

    def test_only_partials_that_may_be_cut_get_an_open_ended_prompt(self):
        st, _ = self.transcriber(SegmentBackend())
        st.prompt_history = "歡迎回到我們的頻道。"
        self.assertEqual(st._prompt("zh", open_ended=True), "歡迎回到我們的頻道")
        self.assertEqual(st._prompt("zh"), "歡迎回到我們的頻道。")  # finals and short partials keep it
        st.prompt_history = 'He said "see you next time."'
        self.assertEqual(st._prompt("en", open_ended=True), 'He said "see you next time')
        backend = SegmentBackend()
        backend.prompts = []
        real = backend.transcribe
        backend.transcribe = lambda audio, lang, prompt, final, *a, **k: (backend.prompts.append((final, prompt)),
                                                                           real(audio, lang, prompt, final, *a, **k))[1]
        st, _ = self.transcriber(backend, pause_at=3.2)
        st.prompt_history = "大家好。"
        st._partial()
        st._partial()
        self.assertEqual(backend.prompts, [(False, "大家好"), (False, "大家好"), (True, "大家好。")])


class CaptionModelTests(unittest.TestCase):
    def test_finished_lines_only(self):
        from app.ui.caption_model import CaptionModel

        model = CaptionModel()
        model.add_final("one")
        model.add_final("two")
        model.set_partial("thr", "")
        self.assertEqual(model.last_lines(2), (["two"], "thr"))
        model.set_show_partial(False)
        self.assertEqual(model.last_lines(2), (["one", "two"], ""))
        self.assertEqual(model.partial, "thr")  # (still there for keyword alerts)

    def test_last_activity_follows_speech(self):
        from app.ui import caption_model

        clock = iter([10.0, 20.0, 30.0, 40.0])
        model = caption_model.CaptionModel()
        self.assertEqual(model.last_activity, 0.0)
        with mock.patch.object(caption_model, "monotonic", lambda: next(clock)):  # (this module's clock only)
            model.set_partial("hel", "")
            self.assertEqual(model.last_activity, 10.0)
            model.add_final("")  # the line was dropped: no speech
            model.set_partial("", "")
            self.assertEqual(model.last_activity, 10.0)
            model.add_final("hello")
            self.assertEqual(model.last_activity, 20.0)
            model.show_partial = False
            model.set_partial("wor", "")  # hidden, but someone is speaking: the overlay must stay up
            self.assertEqual(model.last_activity, 30.0)
            model.add_final("world")
            self.assertEqual(model.last_activity, 40.0)
        model.clear()
        self.assertEqual(model.last_activity, 0.0)


class GpuGenerationTests(unittest.TestCase):
    """What CTranslate2 4.6 reports per GPU generation (src/types.cc: float16 needs tensor cores, int8 6.1)."""

    BASE = {"float32"}

    def test_compute_type(self):
        ampere = self.BASE | {"float16", "bfloat16", "int8", "int8_float16", "int8_float32", "int8_bfloat16"}
        self.assertEqual(pick_compute_type(ampere), "float16")  # RTX 30
        self.assertEqual(pick_compute_type(self.BASE | {"float16", "int8", "int8_float16", "int8_float32"}),
                         "float16")  # GTX 16 / RTX 20 (7.5)
        self.assertEqual(pick_compute_type(self.BASE | {"int8", "int8_float32"}), "int8")  # GTX 10 (6.1)
        self.assertEqual(pick_compute_type(self.BASE), "float32")  # Tesla P100 (6.0), Maxwell

    def test_engine_and_legacy_engine(self):
        gtx970m, gtx1060, gtx680 = (GpuInfo("GTX 970M", 3072, 600, (5, 2)), GpuInfo("GTX 1060", 6144, 5000, (6, 1)),
                                    GpuInfo("GTX 680", 2048, 1800, (3, 0)))
        unknown = GpuInfo("GPU", 4096, 4000)
        with mock.patch("app.asr.cuda_libs.legacy_available", return_value=True):
            self.assertTrue(hardware.needs_legacy_engine(gtx970m))
            self.assertFalse(hardware.needs_legacy_engine(gtx1060))
            self.assertFalse(hardware.too_old_for_gpu_support(gtx970m))
            self.assertTrue(hardware.too_old_for_gpu_support(gtx680))
            self.assertFalse(hardware.too_old_for_gpu_support(unknown))
            with mock.patch("app.asr.cuda_setup.legacy_engine_loaded", return_value=False):
                self.assertFalse(hardware.engine_runs_on(gtx970m))
                self.assertTrue(hardware.engine_runs_on(gtx1060))
                self.assertTrue(hardware.engine_runs_on(unknown))
            with mock.patch("app.asr.cuda_setup.legacy_engine_loaded", return_value=True):
                self.assertTrue(hardware.engine_runs_on(gtx970m))
                self.assertFalse(hardware.engine_runs_on(gtx680))
        with mock.patch("app.asr.cuda_libs.legacy_available", return_value=False):  # (no release pinned)
            self.assertTrue(hardware.too_old_for_gpu_support(gtx970m))
            self.assertFalse(hardware.wants_legacy_engine(gtx970m))

    def test_expected_compute_type_without_cuda(self):
        expected = {(8, 6): "float16", (7, 5): "float16", (6, 1): "int8", (6, 0): "float32", (5, 2): "float32",
                    (0, 0): "float16"}
        for cc, compute_type in expected.items():
            self.assertEqual(hardware.expected_compute_type(GpuInfo("GPU", 4096, 4000, cc)), compute_type, cc)
        # The VRAM estimate doesn't ask CTranslate2 (before GPU support is downloaded, it can't).
        with mock.patch.object(hardware, "cuda_compute_type", side_effect=AssertionError("CUDA touched")), \
                mock.patch.object(hardware, "_supported_compute_types", side_effect=AssertionError("CUDA touched")):
            self.assertIsNone(realtime_risk("turbo", "cuda", GpuInfo("RTX 3050", 4096, 3900, (8, 6))))
            self.assertEqual(realtime_risk("turbo", "cuda", GpuInfo("GTX 970M", 3072, 600, (5, 2))), "vram_low")

    def test_the_gpu_models_run_on(self):
        gtx960, rtx3060 = GpuInfo("GTX 960", 2048, 2000, (5, 2)), GpuInfo("RTX 3060", 12288, 12000, (8, 6))
        # NVML may list them in any order; CUDA counts them in PCI order (cuda_setup sets CUDA_DEVICE_ORDER).
        found = [(rtx3060, (0, 2, 0)), (gtx960, (0, 1, 0))]
        chosen = hardware.choose_gpu(found, pci_order=True)
        self.assertEqual((chosen.name, chosen.cuda_index), ("RTX 3060", 1))
        self.assertEqual(hardware.choose_gpu([(gtx960, (0, 1, 0))], pci_order=True).cuda_index, 0)
        self.assertIsNone(hardware.choose_gpu([], pci_order=True))
        # The user chose CUDA's numbering (CUDA_VISIBLE_DEVICES or another CUDA_DEVICE_ORDER): CUDA's first GPU.
        self.assertEqual(hardware.choose_gpu(found, pci_order=False), rtx3060)
        self.assertEqual(hardware.choose_gpu(found, pci_order=False).cuda_index, 0)
        with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "1"}):
            self.assertFalse(hardware._pci_order())
        with mock.patch.dict(os.environ, {"CUDA_DEVICE_ORDER": "PCI_BUS_ID"}, clear=False):
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            self.assertTrue(hardware._pci_order())

    def test_vram_depends_on_the_compute_type(self):
        gtx970m = GpuInfo("GTX 970M", 3072, 600, (5, 2))
        self.assertEqual(realtime_risk("turbo", "cuda", gtx970m, "float32"), "vram_low")
        self.assertIsNone(realtime_risk("small", "cuda", gtx970m, "float32"))
        self.assertEqual(realtime_risk("medium", "cuda", gtx970m, "float32"), "vram_low")
        self.assertIsNone(realtime_risk("medium", "cuda", gtx970m, "float16"))
        self.assertIsNone(realtime_risk("turbo", "cuda", GpuInfo("GTX 1060", 6144, 5000, (6, 1)), "int8"))


class TranslateTests(unittest.TestCase):
    def transcriber(self, backend, **kw):
        with mock.patch("app.asr.streaming.StreamingVAD"):  # (no ONNX model needed: the audio is handed over)
            return StreamingTranscriber(backend, **kw)

    @staticmethod
    def speak(st, seconds=1.0):
        n = int(seconds * 16000 / FRAME)
        st._utt = _Utterance(start_pos=0, frames=[np.zeros(FRAME, np.float32)] * n, probs=[0.9] * n, decoded_samples=1)
        return st._finalize(force=True)

    def test_translate_reaches_every_decode(self):
        backend = FakeBackend("Hello there, how are you?", "I'm fine, thanks.")
        st = self.transcriber(backend, translate=True)
        self.assertEqual(self.speak(st)[0].text, "Hello there, how are you?")
        self.speak(st)
        self.assertEqual([c["translate"] for c in backend.calls], [True, True])
        self.assertEqual(backend.calls[1]["prompt"], "Hello there, how are you?")  # English prompts English

    def test_switching_drops_the_prompt_in_the_other_language(self):
        backend = FakeBackend("今天天氣很好。", "The weather is nice today.", "Let's go out.")
        st = self.transcriber(backend)
        self.speak(st)
        st.translate = True
        self.speak(st)
        self.assertEqual(backend.calls[1], {"prompt": None, "final": True, "translate": True})
        st.translate = True  # (unchanged: the prompt stays)
        self.speak(st)
        self.assertEqual(backend.calls[2]["prompt"], "The weather is nice today.")


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
    def setUp(self):
        # The presets only: not the models.json of a copy of the app run from source.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ALC_DATA_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        load_catalog.cache_clear()
        self.addCleanup(load_catalog.cache_clear)

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

    def test_model_counts_as_downloaded_with_its_files(self):
        info = get_model("breeze-asr-25")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            d = model_path(info, base)
            d.mkdir()
            (d / "config.json").write_text("{}")
            self.assertFalse(is_downloaded(info, base))
            self.assertEqual(verify(info, base), "missing file model.bin")
            (d / "model.bin").write_bytes(b"x")
            # Without its tokenizer, faster-whisper would fetch one from the internet.
            self.assertFalse(is_downloaded(info, base))
            self.assertEqual(verify(info, base), "missing file tokenizer.json")
            (d / "tokenizer.json").write_text("{}")
            self.assertTrue(is_downloaded(info, base))
            self.assertIsNone(verify(info, base))

    def test_breeze_26_fixed_language_and_borrowed_tokenizer(self):
        taigi = get_model("breeze-asr-26")
        self.assertFalse(taigi.auto_detect)
        self.assertEqual(taigi.default_language, "zh")
        # Its repo has no tokenizer.json; Breeze 25's has the identical vocabulary.
        self.assertEqual(taigi.extra_files, {"tokenizer.json": "SoybeanMilk/faster-whisper-Breeze-ASR-25"})
        self.assertIsNone(get_model("breeze-asr-25").default_language)
        self.assertIsNone(get_model("whisper-large-v3-turbo").default_language)


class PortablePathTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.root / "AppLiveCaption"
        self.app.mkdir()
        for patcher in (mock.patch.object(paths, "app_dir", lambda: self.app),
                        mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "local")})):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("ALC_DATA_DIR", None)

    def test_everything_lives_next_to_the_exe(self):
        self.assertEqual(paths.data_dir(), self.app)
        self.assertEqual(paths.config_file(), self.app / "config.json")
        self.assertEqual(paths.history_dir(), self.app / "history")
        self.assertEqual(paths.log_dir(), self.app / "logs")
        self.assertEqual(paths.default_models_dir(), self.app / "models")

    def test_falls_back_when_the_app_folder_is_read_only(self):
        with mock.patch.object(paths, "_writable", lambda _folder: False):
            self.assertEqual(paths.data_dir(), self.root / "local" / "AppLiveCaption")

    def test_override(self):
        with mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(paths.log_dir(), self.root / "elsewhere" / "logs")

    def test_folder_setting_survives_moving_the_app(self):
        self.assertEqual(paths.to_setting(self.app / "models"), "models")
        self.assertEqual(paths.from_setting("models"), self.app / "models")
        outside = (self.root / "shared models").resolve()
        self.assertEqual(paths.to_setting(outside), str(outside))
        self.assertEqual(paths.from_setting(str(outside)), outside)


class SuggestModelTests(unittest.TestCase):
    """First-time setup's suggested model."""

    PRESETS = [("whisper-large-v3-turbo", "turbo"), ("breeze-asr-25", "large"), ("whisper-tiny", "tiny"),
               ("whisper-base", "base"), ("whisper-small", "small"), ("whisper-medium", "medium")]

    def test_gpu_gets_turbo(self):
        self.assertEqual(suggest_model(self.PRESETS, "cuda", GpuInfo("RTX 4070", 8188, 7000, (8, 9))), "whisper-large-v3-turbo")
        self.assertEqual(suggest_model(self.PRESETS, "cuda", None), "whisper-large-v3-turbo")

    def test_cpu_or_small_gpu_gets_a_lighter_model(self):
        self.assertEqual(suggest_model(self.PRESETS, "cpu", None), "whisper-small")
        # 2 GB is too little for turbo in float16.
        self.assertEqual(suggest_model(self.PRESETS, "cuda", GpuInfo("MX450", 2048, 1800, (7, 5))), "whisper-small")

    def test_a_trimmed_list(self):
        self.assertEqual(suggest_model([("whisper-base", "base"), ("breeze-asr-25", "large")], "cpu", None),
                         "whisper-base")
        self.assertEqual(suggest_model([("breeze-asr-25", "large")], "cpu", None), "breeze-asr-25")
        self.assertIsNone(suggest_model([], "cpu", None))


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    unittest.main()
