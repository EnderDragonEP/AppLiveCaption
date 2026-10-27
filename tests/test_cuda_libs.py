"""Downloading NVIDIA's CUDA libraries (app/asr/cuda_libs.py), with a fake wheel
served from a local file instead of PyPI."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.asr import cuda_libs  # noqa: E402


class PinnedWheelTests(unittest.TestCase):
    def test_pins(self):
        names = set()
        for w in cuda_libs.WHEELS:
            self.assertTrue(w.url.startswith("https://files.pythonhosted.org/"), w.url)
            self.assertTrue(w.url.endswith("-win_amd64.whl"), w.url)
            self.assertIn(f"-{w.version}-", w.url)
            self.assertRegex(w.sha256, r"^[0-9a-f]{64}$")
            names.update(os.path.basename(f) for f in w.files)
        # app/asr/cuda_setup.py only accepts a set with both of these.
        self.assertIn("cublas64_12.dll", names)
        self.assertIn("cudnn_ops64_9.dll", names)
        # CTranslate2 bundles cuDNN's loader; another copy could be a different version.
        self.assertNotIn("cudnn64_9.dll", names)
        self.assertIn("nvidia-cudnn-cu12==9.1.0.70", cuda_libs.BUNDLE_ID)

    def test_legacy_engine_pin(self):
        engine = cuda_libs.LEGACY_ENGINE
        self.assertTrue(engine.url.startswith("https://github.com/EnderDragonEP/AppLiveCaption/releases/download/"))
        self.assertIn(f"-{engine.version}-", engine.url)
        self.assertRegex(cuda_libs.LEGACY_REPLACES_SHA256, r"^[0-9a-f]{64}$")
        if engine.sha256:
            self.assertRegex(engine.sha256, r"^[0-9a-f]{64}$")
            self.assertGreater(engine.size, 0)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(root / "data")})
        env.start()
        self.addCleanup(env.stop)
        self.wheel_path = root / "fake-1.0-py3-none-win_amd64.whl"
        with zipfile.ZipFile(self.wheel_path, "w") as zf:
            zf.writestr("nvidia/fake/bin/a64_1.dll", b"A" * 3000)
            zf.writestr("nvidia/fake/bin/b64_1.dll", b"B" * 5000)
            zf.writestr("nvidia/fake/bin/unused.dll", b"C" * 7000)
            zf.writestr("nvidia/fake/include/a.h", b"//")
        self.wheel = wheel = cuda_libs.Wheel(
            "fake", "1.0", self.wheel_path.as_uri(), hashlib.sha256(self.wheel_path.read_bytes()).hexdigest(),
            self.wheel_path.stat().st_size, ("nvidia/fake/bin/a64_1.dll", "nvidia/fake/bin/b64_1.dll"))
        for name, value in (("WHEELS", (wheel,)), ("BUNDLE_ID", "fake==1.0"), ("DOWNLOAD_SIZE", wheel.size),
                            ("INSTALLED_SIZE", 8000)):
            patcher = mock.patch.object(cuda_libs, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, cancel: threading.Event | None = None) -> list:
        calls = []
        cuda_libs.download(lambda done, total, name: calls.append((done, total, name)), cancel or threading.Event())
        return calls

    def test_download_keeps_only_the_listed_dlls(self):
        calls = self.download()
        d = cuda_libs.lib_dir()
        self.assertEqual(cuda_libs.installed_dir(), d)
        self.assertEqual(sorted(p.name for p in d.iterdir()), [cuda_libs.MARKER, "a64_1.dll", "b64_1.dll"])
        self.assertEqual((d / "b64_1.dll").stat().st_size, 5000)
        self.assertEqual(calls[-1][0], calls[-1][1])  # ends at 100 %
        self.assertFalse(cuda_libs.has_partial_download())

    def test_checksum_mismatch_is_rejected_and_deleted(self):
        with mock.patch.object(cuda_libs, "WHEELS", (replace(self.wheel, sha256="0" * 64),)):
            with self.assertRaises(cuda_libs.DownloadError) as ctx:
                self.download()
        self.assertEqual(ctx.exception.kind, "corrupt")
        self.assertIsNone(cuda_libs.installed_dir())
        self.assertEqual(list((cuda_libs.lib_dir() / "downloads").iterdir()), [])  # not resumed next time

    def test_not_enough_disk_space(self):
        with mock.patch.object(cuda_libs, "INSTALLED_SIZE", 2**60):
            with self.assertRaises(cuda_libs.DownloadError) as ctx:
                self.download()
        self.assertEqual(ctx.exception.kind, "disk_space")

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(cuda_libs.DownloadCancelled):
            self.download(cancel)
        self.assertIsNone(cuda_libs.installed_dir())

    def test_damaged_or_outdated_set_is_not_used(self):
        self.download()
        d = cuda_libs.lib_dir()
        (d / "a64_1.dll").write_bytes(b"short")
        self.assertIsNone(cuda_libs.installed_dir())
        self.assertTrue(cuda_libs.has_partial_download())
        self.download()  # repairs it
        self.assertEqual(cuda_libs.installed_dir(), d)
        with mock.patch.object(cuda_libs, "BUNDLE_ID", "fake==2.0"):  # the app now pins another version
            self.assertIsNone(cuda_libs.installed_dir())

    def legacy_engine(self) -> cuda_libs.Wheel:
        path = Path(self.tmp.name) / "engine.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ctranslate2.dll", b"E" * 4000)
            zf.writestr("LICENSE", b"MIT")
        engine = replace(cuda_libs.LEGACY_ENGINE, url=path.as_uri(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         size=path.stat().st_size)
        patcher = mock.patch.object(cuda_libs, "LEGACY_ENGINE", engine)
        patcher.start()
        self.addCleanup(patcher.stop)
        found = mock.patch("app.asr.cuda_setup.cuda_libraries_found", return_value=False)
        found.start()
        self.addCleanup(found.stop)
        return engine

    def test_legacy_engine_with_the_libraries_or_on_its_own(self):
        engine = self.legacy_engine()
        self.assertEqual(cuda_libs.download_size(legacy=True), self.wheel.size + engine.size)
        calls = []
        cuda_libs.download(lambda *a: calls.append(a), threading.Event(), legacy=True)
        self.assertEqual(calls[-1][:2], (self.wheel.size + engine.size,) * 2)
        self.assertEqual(cuda_libs.installed_dir(), cuda_libs.lib_dir())
        self.assertEqual(cuda_libs.legacy_dll(), cuda_libs.legacy_dir() / "ctranslate2.dll")
        self.assertEqual(cuda_libs.to_download(legacy=True), [])
        # Libraries downloaded before the engine existed (issue #3): only the engine is fetched.
        cuda_libs.remove(in_use=False)
        self.download()
        self.assertIsNone(cuda_libs.legacy_dll())
        self.assertEqual([w for w, _d, _b in cuda_libs.to_download(legacy=True)], [engine])
        calls.clear()
        cuda_libs.download(lambda *a: calls.append(a), threading.Event(), legacy=True)
        self.assertEqual(calls[-1][:2], (engine.size, engine.size))
        self.assertIsNotNone(cuda_libs.legacy_dll())
        self.assertEqual(cuda_libs.installed_dir(), cuda_libs.lib_dir())

    def test_damaged_or_outdated_legacy_engine_is_not_used(self):
        engine = self.legacy_engine()
        cuda_libs.download(lambda *a: None, threading.Event(), legacy=True)
        with mock.patch.object(cuda_libs, "LEGACY_ID", "ctranslate2-legacy-gpu==9.9"):
            self.assertIsNone(cuda_libs.legacy_dll())
        (cuda_libs.legacy_dir() / "ctranslate2.dll").write_bytes(b"short")
        self.assertIsNone(cuda_libs.legacy_dll())
        self.assertEqual(cuda_libs.download_size(legacy=True), engine.size)
        cuda_libs.remove(in_use=True)
        self.assertIsNone(cuda_libs.legacy_dll())

    def test_no_legacy_engine_until_its_release_is_pinned(self):
        with mock.patch.object(cuda_libs, "LEGACY_ENGINE", replace(cuda_libs.LEGACY_ENGINE, sha256="")), \
                mock.patch("app.asr.cuda_setup.cuda_libraries_found", return_value=False):
            self.assertFalse(cuda_libs.legacy_available())
            self.assertEqual([w for w, _d, _b in cuda_libs.to_download(legacy=True)], [self.wheel])

    def test_remove_now_or_at_next_start(self):
        self.download()
        d = cuda_libs.lib_dir()
        cuda_libs.remove(in_use=True)  # loaded DLLs can't be deleted on Windows
        self.assertTrue(cuda_libs.removal_scheduled())
        self.assertIsNone(cuda_libs.installed_dir())
        self.assertTrue((d / "a64_1.dll").exists())
        cuda_libs.cancel_removal()
        self.assertEqual(cuda_libs.installed_dir(), d)
        cuda_libs.remove(in_use=True)
        cuda_libs.remove_if_scheduled()  # next start
        self.assertFalse(d.exists())
        self.download()
        cuda_libs.remove(in_use=False)
        self.assertFalse(d.exists())
        self.assertEqual(cuda_libs.disk_usage(), 0)


if __name__ == "__main__":
    unittest.main()
