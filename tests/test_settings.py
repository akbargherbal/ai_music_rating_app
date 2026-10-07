import os
import sys
import unittest
import tempfile
import json
from pathlib import Path
from rating_app.settings import Settings, load_settings


class TestSettings(unittest.TestCase):
    def test_default_settings(self):
        s = load_settings()
        self.assertEqual(s.host, "127.0.0.1")
        self.assertEqual(s.port, 5000)
        self.assertFalse(s.blind)
        self.assertEqual(s.sources["host"], "default")

    def test_precedence_cli_over_env_and_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "custom.json"
            cfg_file.write_text(json.dumps({"port": 6000, "blind": True}))

            os.environ["RATING_PORT"] = "7000"
            try:
                # Without CLI, env should override file
                s1 = load_settings(config_file_override=str(cfg_file))
                self.assertEqual(s1.port, 7000)
                self.assertTrue(s1.blind)
                self.assertIn("env", s1.sources["port"])

                # With CLI, CLI overrides env
                s2 = load_settings(
                    cli_args={"port": 8000}, config_file_override=str(cfg_file)
                )
                self.assertEqual(s2.port, 8000)
                self.assertEqual(s2.sources["port"], "CLI argument")
            finally:
                os.environ.pop("RATING_PORT", None)

    def test_deprecated_fields_alias(self):
        s = load_settings(cli_args={"fields": "custom_scorecard.json"})
        self.assertEqual(s.scorecard, "custom_scorecard.json")

    def test_audio_rating_json_layer(self):
        with tempfile.TemporaryDirectory() as td:
            audio_dir = Path(td)
            (audio_dir / "_rating.json").write_text(
                json.dumps({"label": "audio_specific_label"})
            )
            s = load_settings(audio_path_override=str(audio_dir))
            self.assertEqual(s.label, "audio_specific_label")
            self.assertIn("_rating.json", s.sources["label"])


if __name__ == "__main__":
    unittest.main()
