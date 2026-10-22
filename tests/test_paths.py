"""Where the app keeps its data: next to the exe (portable), or in the user's profile (installed with Setup)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402
from app.asr import cuda_libs  # noqa: E402


class DataDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.exe_dir = self.root / "AppLiveCaption"
        self.exe_dir.mkdir()
        self.roaming = self.root / "Roaming" / "AppLiveCaption"
        self.local = self.root / "Local" / "AppLiveCaption"
        env = {k: v for k, v in os.environ.items() if k != "ALC_DATA_DIR"}
        env["APPDATA"] = str(self.roaming.parent)
        env["LOCALAPPDATA"] = str(self.local.parent)
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(paths, "app_dir", return_value=self.exe_dir),
                      mock.patch.object(sys, "frozen", True, create=True)):
            patch.start()
            self.addCleanup(patch.stop)
        # is_installed() is cached for the app's lifetime; each test sets up its own copy of the app.
        paths.is_installed.cache_clear()
        self.addCleanup(paths.is_installed.cache_clear)

    def install(self):
        (self.exe_dir / paths.INSTALLED_MARKER).write_text("installed", encoding="utf-8")
        paths.is_installed.cache_clear()

    def test_portable_keeps_everything_next_to_the_exe(self):
        self.assertFalse(paths.is_installed())
        for folder in (paths.data_dir(), paths.local_data_dir(), paths.log_dir().parent,
                       paths.default_models_dir().parent, cuda_libs.lib_dir().parent):
            self.assertEqual(folder, self.exe_dir)
        self.assertEqual(paths.from_setting("models"), self.exe_dir / "models")
        self.assertEqual(paths.to_setting(self.exe_dir / "models"), "models")

    def test_installed_splits_roaming_and_local(self):
        self.install()
        self.assertTrue(paths.is_installed())
        # Small, personal data roams with the profile...
        self.assertEqual(paths.config_file(), self.roaming / "config.json")
        self.assertEqual(paths.history_dir(), self.roaming / "history")
        # ...big or PC-specific data stays local.
        self.assertEqual(paths.log_dir(), self.local / "logs")
        self.assertEqual(paths.default_models_dir(), self.local / "models")
        self.assertEqual(cuda_libs.lib_dir(), self.local / "cuda")
        # The model-folder setting's default ("models") and relative values resolve under the local folder.
        self.assertEqual(paths.from_setting("models"), self.local / "models")
        self.assertEqual(paths.to_setting(self.local / "models"), "models")
        self.assertEqual(paths.to_setting(self.root / "D" / "Models"), str((self.root / "D" / "Models").resolve()))

    def test_alc_data_dir_wins(self):
        self.install()
        with mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(paths.data_dir(), self.root / "elsewhere")
            self.assertEqual(paths.local_data_dir(), self.root / "elsewhere")

    def test_never_installed_from_source(self):
        (self.exe_dir / paths.INSTALLED_MARKER).write_text("installed", encoding="utf-8")
        with mock.patch.object(sys, "frozen", False):
            self.assertFalse(paths.is_installed())


if __name__ == "__main__":
    unittest.main()
