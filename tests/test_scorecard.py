import os
import sys
import unittest
from pathlib import Path
from rating_app.scorecard import (
    load_scorecard,
    validate_scorecard,
    normalize_scorecard,
    list_scorecards,
)


class TestScorecard(unittest.TestCase):
    def test_load_default(self):
        sc = load_scorecard("scorecards/default.json")
        self.assertEqual(sc["summary_metric"], "overall")
        self.assertEqual(sc["schema_version"], 1)
        self.assertTrue(len(sc["criteria"]) >= 5)

    def test_load_arabic_vocal(self):
        sc = load_scorecard("scorecards/arabic_vocal.json")
        keys = [c["key"] for c in sc["criteria"]]
        self.assertIn("melody", keys)
        self.assertIn("prosody", keys)

    def test_validation_errors(self):
        # Empty criteria
        errs = validate_scorecard({"criteria": []})
        self.assertTrue(any("non-empty" in e for e in errs))

        # Duplicate keys
        errs = validate_scorecard(
            {
                "criteria": [
                    {"key": "dup", "type": "rating"},
                    {"key": "dup", "type": "rating"},
                ]
            }
        )
        self.assertTrue(any("Duplicate" in e for e in errs))

        # Invalid summary_metric
        errs = validate_scorecard(
            {
                "summary_metric": "non_existent",
                "criteria": [{"key": "c1", "type": "rating"}],
            }
        )
        self.assertTrue(any("summary_metric" in e for e in errs))

        # Choice with no options
        errs = validate_scorecard(
            {"criteria": [{"key": "c1", "type": "choice", "options": []}]}
        )
        self.assertTrue(any("options" in e for e in errs))

    def test_list_scorecards(self):
        cards = list_scorecards(["scorecards"])
        ids = [c["id"] for c in cards]
        self.assertIn("default", ids)
        self.assertIn("arabic_vocal", ids)


if __name__ == "__main__":
    unittest.main()
