"""Where the app keeps its data: next to the exe (portable) or in %APPDATA% (installed with Setup)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402


class DataDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.exe_dir = self.root / "AppLiveCaption"
        self.exe_dir.mkdir()
        self.appdata = self.root / "Roaming"
        env = {k: v for k, v in os.environ.items() if k != "ALC_DATA_DIR"}
        env["APPDATA"] = str(self.appdata)
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(paths, "app_dir", return_value=self.exe_dir),
                      mock.patch.object(sys, "frozen", True, create=True)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_portable_keeps_data_next_to_the_exe(self):
        self.assertFalse(paths.is_installed())
        self.assertEqual(paths.data_dir(), self.exe_dir)

    def test_installed_keeps_data_in_appdata(self):
        (self.exe_dir / paths.INSTALLED_MARKER).write_text("installed", encoding="utf-8")
        self.assertTrue(paths.is_installed())
        self.assertEqual(paths.data_dir(), self.appdata / "AppLiveCaption")
        self.assertTrue(paths.data_dir().is_dir())
        self.assertEqual(paths.config_file(), self.appdata / "AppLiveCaption" / "config.json")

    def test_alc_data_dir_wins(self):
        (self.exe_dir / paths.INSTALLED_MARKER).write_text("installed", encoding="utf-8")
        with mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(paths.data_dir(), self.root / "elsewhere")

    def test_never_installed_from_source(self):
        (self.exe_dir / paths.INSTALLED_MARKER).write_text("installed", encoding="utf-8")
        with mock.patch.object(sys, "frozen", False):
            self.assertFalse(paths.is_installed())


if __name__ == "__main__":
    unittest.main()
