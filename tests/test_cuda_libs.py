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

    def test_disk_space_counts_the_unpacked_set_once(self):
        second = Path(self.tmp.name) / "fake2-1.0-py3-none-win_amd64.whl"
        with zipfile.ZipFile(second, "w") as zf:
            zf.writestr("nvidia/fake2/bin/c64_1.dll", b"C" * 2000)
        wheel2 = cuda_libs.Wheel("fake2", "1.0", second.as_uri(), hashlib.sha256(second.read_bytes()).hexdigest(),
                                 second.stat().st_size, ("nvidia/fake2/bin/c64_1.dll",))
        Usage = type("Usage", (), {})
        with mock.patch.object(cuda_libs, "WHEELS", (self.wheel, wheel2)), \
                mock.patch.object(cuda_libs, "INSTALLED_SIZE", 10**9):
            need = cuda_libs.INSTALLED_SIZE + max(self.wheel.size, wheel2.size) + cuda_libs.DISK_MARGIN
            for free, ok in ((need - 1, False), (need, True)):
                usage = Usage()
                usage.free = free
                with mock.patch.object(cuda_libs.shutil, "disk_usage", return_value=usage):
                    if ok:
                        self.download()
                        self.assertEqual(cuda_libs.installed_dir(), cuda_libs.lib_dir())
                    else:
                        with self.assertRaises(cuda_libs.DownloadError):
                            self.download()

    def test_libraries_stay_installed_when_the_engine_download_fails(self):
        engine = self.legacy_engine()
        with mock.patch.object(cuda_libs, "LEGACY_ENGINE", replace(engine, sha256="0" * 64)):
            with self.assertRaises(cuda_libs.DownloadError) as ctx:
                cuda_libs.download(lambda *a: None, threading.Event(), legacy=True)
            self.assertEqual(ctx.exception.kind, "corrupt")
            self.assertEqual(cuda_libs.installed_dir(), cuda_libs.lib_dir())  # (marked before the engine)
            self.assertEqual([w.name for w, _d, _b in cuda_libs.to_download(legacy=True)], [engine.name])
        cuda_libs.download(lambda *a: None, threading.Event(), legacy=True)  # Resume: only the engine
        self.assertIsNotNone(cuda_libs.legacy_dll())

    def test_a_complete_engine_alone_is_not_a_partial_download(self):
        self.legacy_engine()
        with mock.patch("app.asr.cuda_setup.cuda_libraries_found", return_value=True):  # (libraries elsewhere)
            cuda_libs.download(lambda *a: None, threading.Event(), legacy=True)
        self.assertIsNone(cuda_libs.installed_dir())
        self.assertIsNotNone(cuda_libs.legacy_dll())
        self.assertFalse(cuda_libs.has_partial_download())
        (cuda_libs.legacy_dir() / cuda_libs.MARKER).unlink()  # an engine download that didn't finish
        self.assertTrue(cuda_libs.has_partial_download())

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


class LegacyEngineLoadingTests(unittest.TestCase):
    """app/asr/cuda_setup.py's checks around loading the legacy engine (the load itself needs a GPU)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(Path(self.tmp.name) / "data")})
        env.start()
        self.addCleanup(env.stop)

    def test_only_ctranslate2s_own_dll_is_hidden(self):
        from app.asr.cuda_setup import _without_dll

        root = Path(self.tmp.name)
        for folder in ("ctranslate2", "other"):
            (root / folder).mkdir()
            for name in ("ctranslate2.dll", "libiomp5md.dll"):
                (root / folder / name).write_bytes(b"")
        import glob as glob_module
        wrapped = _without_dll(str(root / "ctranslate2"), glob_module.glob)
        names = lambda folder: sorted(os.path.basename(f) for f in wrapped(os.path.join(root, folder, "*.dll")))
        self.assertEqual(names("ctranslate2"), ["libiomp5md.dll"])
        self.assertEqual(names("other"), ["ctranslate2.dll", "libiomp5md.dll"])

    def test_ctranslate2_still_loads_its_dlls_with_glob(self):
        # The legacy engine relies on this (cuda_setup._load_legacy_engine): a CTranslate2 update that loads its
        # DLLs another way must fail here, not in the app.
        from app.asr.cuda_setup import _ctranslate2_dir

        package = _ctranslate2_dir()
        if package is None:
            self.skipTest("CTranslate2 isn't installed")
        source = (Path(package) / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('glob.glob(os.path.join(package_dir, "*.dll"))', source)

    def test_the_official_dll_is_hashed_once(self):
        from app.asr import cuda_setup

        dll = Path(self.tmp.name) / "ctranslate2.dll"
        dll.write_bytes(b"x" * 100)
        cuda_libs.legacy_dir().mkdir(parents=True)
        digest = hashlib.sha256(dll.read_bytes()).hexdigest()
        with mock.patch.object(cuda_libs, "LEGACY_REPLACES_SIZE", 100), \
                mock.patch.object(cuda_libs, "LEGACY_REPLACES_SHA256", digest), \
                mock.patch.object(cuda_libs, "file_sha256", wraps=cuda_libs.file_sha256) as hashed:
            self.assertTrue(cuda_setup._is_official_dll(str(dll)))
            self.assertTrue(cuda_setup._is_official_dll(str(dll)))
            self.assertEqual(hashed.call_count, 1)  # the second start uses the remembered result
            os.utime(dll, ns=(1, 1))  # (replaced by another file of the same size)
            self.assertTrue(cuda_setup._is_official_dll(str(dll)))
            self.assertEqual(hashed.call_count, 2)
            dll.write_bytes(b"y" * 100)
            self.assertFalse(cuda_setup._is_official_dll(str(dll)))
            dll.write_bytes(b"x" * 99)
            self.assertFalse(cuda_setup._is_official_dll(str(dll)))
            self.assertEqual(hashed.call_count, 3)  # (a different size isn't hashed)


if __name__ == "__main__":
    unittest.main()
