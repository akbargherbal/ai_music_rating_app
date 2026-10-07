import os
import sys
import unittest
import tempfile
import json
from pathlib import Path
from rating_app.store import Run, atomic_write_json, migrate_legacy_evaluations


class TestStore(unittest.TestCase):
    def test_run_creation_and_atomic_save(self):
        with tempfile.TemporaryDirectory() as td:
            sc = {"name": "Test", "criteria": [{"key": "overall", "type": "rating"}]}
            run = Run(
                "run_001", td, audio="/dummy/audio", label="Run One", scorecard=sc
            )
            self.assertTrue(run.snapshot_path.is_file())
            self.assertTrue(run.run_meta_path.is_file())

            run.set_track_evaluation(
                "arm1/track1.wav", {"overall": 5, "notes": "Solid"}
            )
            self.assertTrue(run.results_path.is_file())

            # Reload run
            run2 = Run("run_001", td)
            eval1 = run2.get_track_evaluation("arm1/track1.wav")
            self.assertEqual(eval1["overall"], 5)
            self.assertEqual(eval1["notes"], "Solid")

    def test_scorecard_drift_detection(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot_sc = {
                "name": "Old",
                "criteria": [
                    {"key": "overall", "type": "rating"},
                    {"key": "mix", "type": "rating"},
                ],
            }
            run = Run("run_002", td, scorecard=snapshot_sc)

            # Active scorecard has 'melody' added and 'mix' removed
            active_sc = {
                "name": "New",
                "criteria": [
                    {"key": "overall", "type": "rating"},
                    {"key": "melody", "type": "rating"},
                ],
            }
            drift = run.detect_drift(active_sc)
            self.assertTrue(drift["drifted"])
            self.assertEqual(drift["orphaned_keys"], ["mix"])
            self.assertEqual(drift["new_keys"], ["melody"])

    def test_legacy_migration(self):
        with tempfile.TemporaryDirectory() as td:
            legacy_file = Path(td) / "evaluations.json"
            legacy_file.write_text(
                json.dumps(
                    {"meta": {"label": "old_run"}, "tracks": {"t1.wav": {"overall": 3}}}
                )
            )

            target_file = Path(td) / "migrated_results.json"
            success = migrate_legacy_evaluations(legacy_file, target_file)
            self.assertTrue(success)
            self.assertTrue(target_file.is_file())

            data = json.loads(target_file.read_text())
            self.assertEqual(data["tracks"]["t1.wav"]["overall"], 3)
            self.assertEqual(data["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
