import os
import sys
import types
import json
import tempfile
import unittest
from pathlib import Path

# Provide flask stub if flask is not installed
if "flask" not in sys.modules:
    try:
        import flask
    except ImportError:
        flask_stub = types.ModuleType("flask")
        for attr in [
            "Flask",
            "Response",
            "abort",
            "redirect",
            "render_template_string",
            "render_template",
            "request",
            "send_from_directory",
            "url_for",
            "jsonify",
        ]:
            setattr(flask_stub, attr, lambda *a, **kw: None)
        sys.modules["flask"] = flask_stub

# Import baseline functions from rating_app modules
from rating_app.scorecard import normalize_criteria
from rating_app.discovery import discover, is_done
from rating_app.report import render_markdown


class TestBaseline(unittest.TestCase):
    def test_normalize_criteria(self):
        raw = [
            {"key": "test_rating"},
            {"key": "test_choice", "type": "choice", "options": ["a", "b"]},
            {"key": "test_notes", "type": "notes"},
        ]
        norm = normalize_criteria(raw)
        self.assertEqual(len(norm), 3)
        self.assertEqual(norm[0]["type"], "rating")
        self.assertEqual(norm[0]["max"], 5)
        self.assertTrue(norm[0]["required"])
        self.assertEqual(
            norm[1]["options"], [{"v": "a", "t": "a"}, {"v": "b", "t": "b"}]
        )
        self.assertFalse(norm[2]["required"])

    def test_is_done(self):
        criteria = normalize_criteria(
            [{"key": "q1", "type": "rating"}, {"key": "notes", "type": "notes"}]
        )
        self.assertFalse(is_done({}, criteria))
        self.assertFalse(is_done({"q1": ""}, criteria))
        self.assertTrue(is_done({"q1": "4"}, criteria))
        self.assertTrue(is_done({"q1": "4", "notes": ""}, criteria))

    def test_discover(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            arm1 = root / "arm1"
            arm1.mkdir()
            (arm1 / "take1.wav").write_bytes(b"dummy")
            (arm1 / "take2.mp3").write_bytes(b"dummy")
            (arm1 / "ignore.txt").write_text("text")
            (arm1 / "_knob.json").write_text(json.dumps({"label": "Arm 1 Label"}))

            root_labels = {"arm1/take1.wav": "Custom Take 1"}
            (root / "labels.json").write_text(json.dumps(root_labels))

            tracks = discover(root)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]["group"], "arm1")
            self.assertEqual(tracks[0]["group_label"], "Arm 1 Label")
            self.assertEqual(tracks[0]["label"], "Custom Take 1")
            self.assertEqual(tracks[1]["label"], "take2")

    def test_render_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tracks = [
                {
                    "name": "arm1/take1.wav",
                    "file": "arm1/take1.wav",
                    "stem": "take1",
                    "group": "arm1",
                    "group_label": "Arm One",
                    "label": "Take 1",
                }
            ]
            sc = {
                "name": "Test Scorecard",
                "schema_version": 1,
                "criteria": normalize_criteria(
                    [
                        {
                            "key": "overall",
                            "label": "Overall",
                            "type": "rating",
                            "max": 5,
                        },
                        {"key": "notes", "label": "Notes", "type": "notes"},
                    ]
                ),
            }
            results = {
                "meta": {"label": "test_run"},
                "tracks": {"arm1/take1.wav": {"overall": 4, "notes": "Great take"}},
            }
            md = render_markdown(tracks, results, sc, root, "test_run")
            self.assertIn("# Listening rating report — test_run", md)
            self.assertIn("Arm One", md)
            self.assertIn("Take 1", md)
            self.assertIn("Great take", md)


if __name__ == "__main__":
    unittest.main()
