"""Version parsing for the GitHub update check (same cases as HorizontalScroll)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.updates import is_newer, parse_version, version_from_tag  # noqa: E402


class UpdateVersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_version("1.2"), (1, 2, 0))
        self.assertEqual(parse_version("V2"), (2, 0, 0))
        self.assertEqual(parse_version("1.4.0-beta.2"), (1, 4, 0))

    def test_version_from_tag(self):
        self.assertEqual(version_from_tag("v1.2.3"), "1.2.3")
        self.assertEqual(version_from_tag("V1.2.3"), "1.2.3")
        self.assertEqual(version_from_tag("v.1.0.1"), "1.0.1")
        self.assertEqual(version_from_tag("1.0.0"), "1.0.0")

    def test_is_newer(self):
        self.assertTrue(is_newer("1.0.1", "1.0.0"))
        self.assertTrue(is_newer("v1.10.0", "1.9.9"))  # numeric, not alphabetical
        self.assertTrue(is_newer("2", "1.9"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_newer("v1.0", "1.0.0"))
        self.assertFalse(is_newer("0.9.0", "1.0.0"))


if __name__ == "__main__":
    unittest.main()
