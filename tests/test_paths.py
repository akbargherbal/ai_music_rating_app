import os
import sys
import unittest
import tempfile
from pathlib import Path
from rating_app.paths import browse_directory, is_path_within


class TestPaths(unittest.TestCase):
    def test_is_path_within(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sub = root / "sub"
            sub.mkdir()
            self.assertTrue(is_path_within(sub, root))
            self.assertFalse(is_path_within(root.parent, root))

    def test_browse_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            sub = root / "subfolder"
            sub.mkdir()
            (root / "test1.json").write_text("{}")
            (root / "test2.wav").write_bytes(b"dummy")

            res = browse_directory(str(root), allowed_extensions=(".json",))
            dir_names = [d["name"] for d in res["dirs"]]
            file_names = [f["name"] for f in res["files"]]
            self.assertIn("subfolder", dir_names)
            self.assertIn("test1.json", file_names)
            self.assertNotIn("test2.wav", file_names)


if __name__ == "__main__":
    unittest.main()
