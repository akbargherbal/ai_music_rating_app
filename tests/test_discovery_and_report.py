import os
import sys
import unittest
import tempfile
import json
from pathlib import Path
from rating_app.discovery import discover, sections_of, is_done
from rating_app.report import render_markdown, render_csv, render_json
from rating_app.settings import Settings


class TestDiscoveryAndReport(unittest.TestCase):
    def test_discovery_with_rich_metadata_and_blinding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            arm1 = root / "prompt_a"
            arm1.mkdir()
            (arm1 / "_meta.json").write_text(
                json.dumps(
                    {
                        "label": "Prompt A",
                        "model": "vocal-large-1",
                        "prompt": "sing in maqam rast",
                    }
                )
            )
            (arm1 / "take_01.wav").write_bytes(b"dummy")
            (arm1 / "take_01.json").write_text(json.dumps({"seed": 1234, "cfg": 7.5}))

            # 1. Unblinded discovery
            tracks = discover(root, blind=False)
            self.assertEqual(len(tracks), 1)
            t = tracks[0]
            self.assertEqual(t["group_label"], "Prompt A")
            self.assertEqual(t["metadata"]["model"], "vocal-large-1")
            self.assertEqual(t["metadata"]["seed"], 1234)

            # 2. Blinded discovery
            blind_tracks = discover(root, blind=True, blind_seed=99)
            self.assertEqual(len(blind_tracks), 1)
            bt = blind_tracks[0]
            self.assertEqual(bt["label"], "Track 01")
            self.assertEqual(bt["group"], "blinded")
            self.assertEqual(bt["raw_group"], "prompt_a")

    def test_report_generation_multi_metric(self):
        tracks = [
            {
                "name": "arm1/take1.wav",
                "file": "arm1/take1.wav",
                "stem": "take1",
                "group": "arm1",
                "group_label": "Arm One",
                "label": "Take 1",
                "raw_group": "arm1",
                "raw_group_label": "Arm One",
                "raw_label": "Take 1",
                "metadata": {"seed": 123, "model": "test-v1"},
            }
        ]
        scorecard = {
            "name": "Multi-Metric",
            "schema_version": 1,
            "criteria": [
                {"key": "overall", "label": "Overall", "type": "rating", "max": 5},
                {"key": "fidelity", "label": "Fidelity", "type": "rating", "max": 5},
                {
                    "key": "artifacts",
                    "label": "Artifacts",
                    "type": "choice",
                    "options": [{"v": "none", "t": "None"}],
                },
                {"key": "notes", "label": "Notes", "type": "notes"},
            ],
        }
        results = {
            "tracks": {
                "arm1/take1.wav": {
                    "overall": 4,
                    "fidelity": 5,
                    "artifacts": "none",
                    "notes": "Crisp",
                }
            }
        }
        settings = Settings(report_columns=["seed", "model"])

        # Markdown
        md = render_markdown(
            tracks, results, scorecard, "/audio", "test_report", settings=settings
        )
        self.assertIn("### Metric: **Overall**", md)
        self.assertIn("### Metric: **Fidelity**", md)
        self.assertIn("Choice Distribution: **Artifacts**", md)
        self.assertIn("Meta: seed", md)
        self.assertIn("Crisp", md)

        # CSV
        csv_out = render_csv(tracks, results, scorecard, settings=settings)
        self.assertIn(
            "file,folder_arm,track_label,done,overall,fidelity,artifacts,notes,seed,model",
            csv_out,
        )
        self.assertIn(
            "arm1/take1.wav,arm1,Take 1,1,4,5,none,Crisp,123,test-v1", csv_out
        )

        # JSON
        json_out = render_json(tracks, results, scorecard, settings=settings)
        self.assertEqual(json_out["summary"]["ratings"]["overall"]["mean"], 4.0)
        self.assertEqual(json_out["summary"]["ratings"]["fidelity"]["mean"], 5.0)


if __name__ == "__main__":
    unittest.main()
