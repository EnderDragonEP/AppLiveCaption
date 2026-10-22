"""The model list the user can change: add, remove, reset, and checking/importing a model."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import catalog, storage  # noqa: E402
from app.models.custom import (  # noqa: E402
    CustomModelError,
    check_files,
    estimate_tier,
    inspect_folder,
    inspect_repo,
    parse_repo_id,
)
from app.models.downloader import DownloadCancelled, DownloadError, download_model  # noqa: E402
from app.models.storage import is_downloaded, model_path, verify  # noqa: E402

PRESET_COUNT = len(json.loads(Path(catalog.__file__).with_name("catalog.json").read_text(encoding="utf-8"))["models"])


class CatalogTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"ALC_DATA_DIR": str(self.root / "data")})
        env.start()
        self.addCleanup(env.stop)
        catalog.load_catalog.cache_clear()
        self.addCleanup(catalog.load_catalog.cache_clear)

    def entry(self, name="My model", repo="someone/faster-whisper-mine", **extra) -> dict:
        return {"name": name, "repo": repo, "backend": "faster-whisper", "languages": "whisper", "size_mb": 100,
                "license": "MIT", **extra}


class CatalogListTests(CatalogTestCase):
    def test_default_list_is_the_presets(self):
        self.assertEqual(len(catalog.load_catalog()), PRESET_COUNT)
        self.assertTrue(catalog.is_default_catalog())
        self.assertFalse((self.root / "data" / "models.json").exists())

    def test_remove_a_preset_and_reset(self):
        catalog.remove_model("whisper-tiny")
        self.assertIsNone(catalog.get_model("whisper-tiny"))
        self.assertEqual(len(catalog.load_catalog()), PRESET_COUNT - 1)
        self.assertFalse(catalog.is_default_catalog())
        self.assertEqual(catalog.reset_catalog(), [])
        self.assertIsNotNone(catalog.get_model("whisper-tiny"))
        self.assertTrue(catalog.is_default_catalog())
        self.assertFalse((self.root / "data" / "models.json").exists())  # nothing left to remember

    def test_remove_everything_then_restore(self):
        for m in catalog.load_catalog():
            catalog.remove_model(m.id)
        self.assertEqual(catalog.load_catalog(), ())
        self.assertIsNone(catalog.default_model_id())
        catalog.reset_catalog()
        self.assertEqual(len(catalog.load_catalog()), PRESET_COUNT)
        self.assertEqual(catalog.default_model_id(), "whisper-large-v3-turbo")

    def test_add_a_custom_model_and_it_survives_a_restart(self):
        info = catalog.add_custom_model(self.entry())
        self.assertTrue(info.custom)
        self.assertEqual(info.id, "my-model")
        self.assertEqual(catalog.load_catalog()[-1], info)
        catalog.load_catalog.cache_clear()  # as after a restart: read again from models.json
        self.assertEqual(catalog.get_model("my-model").repo, "someone/faster-whisper-mine")

    def test_ids_never_clash(self):
        a = catalog.add_custom_model(self.entry("Whisper tiny", "a/one"))  # same slug as a preset's id
        b = catalog.add_custom_model(self.entry("Whisper tiny", "b/two"))
        self.assertEqual(len({a.id, b.id, "whisper-tiny"}), 3)

    def test_adding_a_listed_repo_changes_nothing(self):
        info = catalog.add_custom_model(self.entry("Tiny again", "Systran/faster-whisper-tiny"))
        self.assertEqual(info.id, "whisper-tiny")
        self.assertEqual(len(catalog.load_catalog()), PRESET_COUNT)

    def test_adding_a_removed_presets_repo_restores_the_preset(self):
        catalog.remove_model("whisper-tiny")
        info = catalog.add_custom_model(self.entry("Tiny again", "systran/faster-whisper-tiny"))
        self.assertEqual(info.id, "whisper-tiny")
        self.assertFalse(info.custom)
        self.assertTrue(catalog.is_default_catalog())

    def test_reset_returns_the_models_it_dropped(self):
        catalog.add_custom_model(self.entry())
        catalog.remove_model("whisper-base")
        dropped = catalog.reset_catalog()
        self.assertEqual([m.id for m in dropped], ["my-model"])
        self.assertTrue(catalog.is_default_catalog())

    def test_removing_a_custom_model_forgets_it(self):
        catalog.add_custom_model(self.entry())
        catalog.remove_model("my-model")
        self.assertIsNone(catalog.get_model("my-model"))
        self.assertTrue(catalog.is_default_catalog())

    def test_a_broken_models_json_is_ignored(self):
        path = self.root / "data" / "models.json"
        path.parent.mkdir(parents=True)
        for text in ("{not json", "[]", json.dumps({"removed": 5, "custom": [{"name": "no id"}, 3]})):
            path.write_text(text, encoding="utf-8")
            catalog.load_catalog.cache_clear()
            self.assertEqual(len(catalog.load_catalog()), PRESET_COUNT, text)

    def test_a_model_needs_a_name(self):
        with self.assertRaises(ValueError):
            catalog.add_custom_model(self.entry(" "))
        self.assertTrue(catalog.is_default_catalog())

    def test_a_presets_repo_is_added_without_checking_it_online(self):
        # Breeze ASR 26's repository has no tokenizer.json (it comes from another repository).
        catalog.remove_model("breeze-asr-26")
        with mock.patch("huggingface_hub.HfApi", side_effect=AssertionError("no network")):
            entry = inspect_repo("https://huggingface.co/SoybeanMilk/faster-whisper-Breeze-ASR-26")
        info = catalog.add_custom_model(entry)
        self.assertEqual(info.id, "breeze-asr-26")
        self.assertTrue(catalog.is_default_catalog())

    def test_an_added_model_never_shadows_a_preset(self):
        # As when a later catalog.json gains a preset the user had added themselves.
        path = self.root / "data" / "models.json"
        path.parent.mkdir(parents=True)
        custom = [self.entry("Clash", "x/y", id="whisper-tiny"),
                  self.entry("Same repo", "systran/faster-whisper-base", id="mine"),
                  self.entry("Fine", "a/b", id="ok"),
                  self.entry("Same id", "c/d", id="ok"),
                  self.entry("A removed preset's repo", "Systran/faster-whisper-small", id="small")]
        path.write_text(json.dumps({"removed": ["whisper-small"], "custom": custom}), encoding="utf-8")
        catalog.load_catalog.cache_clear()
        ids = [m.id for m in catalog.load_catalog()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), PRESET_COUNT)  # one preset removed, one model added
        self.assertFalse(catalog.get_model("whisper-tiny").custom)
        self.assertEqual(catalog.get_model("ok").name, "Fine")
        catalog.remove_model("whisper-tiny")  # the preset, not the hidden model with its id
        self.assertIsNone(catalog.get_model("whisper-tiny"))
        self.assertEqual([m.id for m in catalog.reset_catalog()], ["ok"])  # (so only its files are deleted)

    def test_local_models_get_their_own_folder(self):
        info = catalog.add_custom_model(self.entry("Mine", "", id="local-mine", source_folder=str(self.root)))
        self.assertEqual(info.repo, "")
        self.assertEqual(info.folder_name, "local--local-mine")
        self.assertEqual(catalog.get_model("whisper-tiny").folder_name, "Systran--faster-whisper-tiny")


class CheckTests(unittest.TestCase):
    def test_check_files(self):
        self.assertIsNone(check_files(["model.bin", "config.json", "tokenizer.json", "vocabulary.json", "README.md"]))
        self.assertIn("model.bin is missing", check_files(["config.json", "model.safetensors", "tokenizer.json"]))
        self.assertIn("tokenizer.json is missing", check_files(["model.bin", "config.json"]))
        # Files in sub-folders don't count: the model must be at the top of the repository.
        self.assertIn("missing", check_files(["sub/model.bin", "sub/config.json", "sub/tokenizer.json"]))
        self.assertNotEqual(check_files(["config.json"]), check_files(["config.json"], local=True))

    def test_estimate_tier(self):
        mb = 2**20
        self.assertEqual([estimate_tier(n * mb) for n in (75, 145, 480, 1530, 3090)],
                         ["tiny", "base", "small", "medium", "large"])

    def test_parse_repo_id(self):
        self.assertEqual(parse_repo_id(" Systran/faster-whisper-large-v3 "), "Systran/faster-whisper-large-v3")
        self.assertEqual(parse_repo_id("https://huggingface.co/Systran/faster-whisper-tiny/tree/main"),
                         "Systran/faster-whisper-tiny")
        for bad in ("", "no-slash", "a/b/c", "https://example.com/a/b", "a b/c"):
            with self.assertRaises(CustomModelError, msg=bad):
                parse_repo_id(bad)


class LocalFolderTests(CatalogTestCase):
    def setUp(self):
        super().setUp()
        self.base = self.root / "models"
        self.base.mkdir()
        patch = mock.patch.object(storage, "models_dir", return_value=self.base)
        patch.start()
        self.addCleanup(patch.stop)

    def make_model(self, name="converted", **files) -> Path:
        folder = self.root / name
        folder.mkdir(parents=True)
        contents = {"model.bin": b"x" * 5000, "config.json": b"{}", "tokenizer.json": b"{}",
                    "vocabulary.json": b"[]", "README.md": b"docs", ".gitattributes": b"x"}
        contents.update(files)
        for n, data in contents.items():
            if data is not None:
                (folder / n).write_bytes(data)
        return folder

    def test_inspect_folder(self):
        folder = self.make_model()
        entry = inspect_folder(str(folder), " My own ")
        self.assertEqual((entry["name"], entry["repo"], entry["id"]), ("My own", "", "local-converted"))
        self.assertEqual(entry["source_folder"], str(folder.resolve()))
        self.assertEqual(entry["tier"], "tiny")
        with self.assertRaises(CustomModelError):
            inspect_folder(str(self.make_model("broken", **{"model.bin": None})))
        with self.assertRaises(CustomModelError):
            inspect_folder(str(self.root / "missing"))

    def test_copy_into_the_models_folder(self):
        info = catalog.add_custom_model(inspect_folder(str(self.make_model())))
        base = self.root / "models"
        calls = []
        path = download_model(info, calls.append, threading.Event(), base=base)
        self.assertEqual(path, model_path(info, base))
        self.assertEqual(sorted(p.name for p in path.iterdir() if p.is_file()),
                         [".alc_complete.json", "config.json", "model.bin", "tokenizer.json", "vocabulary.json"])
        self.assertTrue(is_downloaded(info, base))
        self.assertIsNone(verify(info, base))
        self.assertEqual((calls[-1].done, calls[-1].total), (calls[-1].total, calls[-1].total))

    def test_a_drive_root(self):
        folder = self.make_model()

        class DriveRoot(type(folder)):  # as D:\ has no name
            @property
            def name(self):
                return "" if self == folder else super().name

        with mock.patch("app.models.custom.Path", DriveRoot):
            entry = inspect_folder(str(folder))
        self.assertEqual((entry["name"], entry["id"]), ("Local model", "local-Local model"))
        self.assertEqual(catalog.add_custom_model(entry).id, "local-local-model")

    def test_sub_folders_are_not_copied(self):
        # Only the top of the folder is checked and counted in the size, so only that is copied.
        folder = self.make_model()
        (folder / "original").mkdir()
        (folder / "original" / "pytorch_model.bin").write_bytes(b"x" * 2**20 * 3)
        entry = inspect_folder(str(folder))
        self.assertEqual(entry["size_mb"], 1)
        path = download_model(catalog.add_custom_model(entry), lambda p: None, threading.Event(), base=self.base)
        self.assertFalse((path / "original").exists())

    def test_files_left_by_a_removed_model_are_not_taken_for_a_new_ones(self):
        first = catalog.add_custom_model(inspect_folder(str(self.make_model("a/mine"))))
        download_model(first, lambda p: None, threading.Event(), base=self.base)
        catalog.remove_model(first.id)  # its files stay on disk
        # Another model from a folder of the same name, with a model.bin of the same size.
        second = catalog.add_custom_model(inspect_folder(str(self.make_model("b/mine", **{"model.bin": b"y" * 5000}))))
        self.assertNotEqual(second.folder_name, first.folder_name)
        self.assertFalse(is_downloaded(second, self.base))
        path = download_model(second, lambda p: None, threading.Event(), base=self.base)
        self.assertEqual((path / "model.bin").read_bytes(), b"y" * 5000)

    def test_cancel_then_resume(self):
        info = catalog.add_custom_model(inspect_folder(str(self.make_model())))
        base = self.root / "models"
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DownloadCancelled):
            download_model(info, lambda p: None, cancel, base=base)
        self.assertFalse(is_downloaded(info, base))
        download_model(info, lambda p: None, threading.Event(), base=base)
        self.assertTrue(is_downloaded(info, base))

    def test_the_folder_vanished(self):
        folder = self.make_model()
        info = catalog.add_custom_model(inspect_folder(str(folder)))
        for p in folder.iterdir():
            p.unlink()
        folder.rmdir()
        with self.assertRaises(DownloadError) as ctx:
            download_model(info, lambda p: None, threading.Event(), base=self.root / "models")
        self.assertEqual(ctx.exception.kind, "not_found")


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    unittest.main()
