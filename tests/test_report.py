"""rating_app.report - statistics helpers and the Markdown / CSV / JSON renderers."""

from __future__ import annotations

import csv
import io
import json
import math

import pytest

from rating_app.report import _calc_stats, _to_float, render_csv, render_json, render_markdown
from rating_app.scorecard import normalize_criteria
from rating_app.settings import Settings


# ------------------------------------------------------------------ fixtures
def make_track(name="arm1/take1.wav", group="arm1", group_label="Arm One", label="Take 1", **extra):
    t = {
        "name": name, "file": name, "stem": name.rsplit("/", 1)[-1].split(".")[0],
        "group": group, "group_label": group_label, "label": label,
        "raw_group": group, "raw_group_label": group_label, "raw_label": label,
        "metadata": {"seed": 123, "model": "test-v1"},
    }
    t.update(extra)
    return t


@pytest.fixture()
def scorecard():
    return {
        "name": "Multi-Metric",
        "schema_version": 1,
        "criteria": normalize_criteria(
            [
                {"key": "overall", "label": "Overall", "type": "rating", "max": 5},
                {"key": "fidelity", "label": "Fidelity", "type": "rating", "max": 5},
                {"key": "artifacts", "label": "Artifacts", "type": "choice", "options": ["none", "some"]},
                {"key": "tags", "label": "Tags", "type": "multi_choice", "options": ["x", "y"]},
                {"key": "notes", "label": "Notes", "type": "notes"},
            ]
        ),
    }


@pytest.fixture()
def tracks():
    return [make_track()]


@pytest.fixture()
def results():
    return {
        "meta": {"label": "r"},
        "tracks": {
            "arm1/take1.wav": {"overall": 4, "fidelity": 5, "artifacts": "none",
                               "tags": ["x", "y"], "notes": "Crisp"},
        },
    }


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize("val, expected", [("4", 4.0), (3, 3.0), ("2.5", 2.5), (None, None),
                                           ("", None), ("abc", None), ([1], None)])
def test_to_float(val, expected):
    assert _to_float(val) == expected


def test_calc_stats():
    assert _calc_stats([]) == (0.0, 0.0, 0)
    assert _calc_stats([4]) == (4.0, 0.0, 1)
    mean, std, n = _calc_stats([2, 4, 6])
    assert (mean, n) == (4.0, 3)
    assert std == pytest.approx(2.0)  # sample (n-1) stddev
    assert _calc_stats([1, 1])[1] == 0.0
    assert math.isfinite(_calc_stats([0.1, 0.7, 0.4])[1])


# ------------------------------------------------------------------ markdown
def test_markdown_multi_metric(tracks, results, scorecard):
    settings = Settings(report_columns=["seed", "model"])
    md = render_markdown(tracks, results, scorecard, "/audio", "test_report", settings=settings)
    assert "# Listening rating report — test_report" in md
    assert "- **Scorecard**: Multi-Metric (schema v1)" in md
    assert "- **Tracks**: 1 across 1 folder arms" in md
    assert "- **Metadata Columns**: seed, model" in md
    assert "- **Blind Mode**: False" in md
    assert "### Metric: **Overall** (1..5)" in md and "### Metric: **Fidelity**" in md
    assert "| Arm One | 4.00 | 0.00 | 1 |" in md
    assert "Choice Distribution: **Artifacts**" in md
    assert "| none | 1 | 100.0% |" in md
    assert "| x | 1 | 50.0% |" in md  # multi_choice lists are counted per option
    assert "Meta: seed" in md and "| 123 | test-v1 |" in md
    assert "### `Take 1` (arm1/take1.wav)" in md and "- notes: Crisp" in md
    assert "x, y" in md  # list answers joined in score table
    assert "## Raw Evaluation Data" in md and '"overall": 4' in md


def test_markdown_accepts_flat_results_mapping(tracks, scorecard):
    md = render_markdown(tracks, {"arm1/take1.wav": {"overall": 2}}, scorecard, "/a", "L")
    assert "| Arm One | 2.00 | 0.00 | 1 |" in md


def test_markdown_orphaned_answers_are_listed_but_updated_is_not(tracks, scorecard):
    res = {"tracks": {"arm1/take1.wav": {"overall": 3, "ghost": "x", "updated": "now"}}}
    md = render_markdown(tracks, res, scorecard, "/a", "L")
    assert "Orphaned answers" in md and "`ghost`" in md and "`updated`" not in md


def test_markdown_orphan_scan_skips_non_dict_entries(tracks, scorecard):
    res = {"tracks": {"arm1/take1.wav": {"overall": 3}, "stray": "junk"}}
    assert "Orphaned" not in render_markdown(tracks, res, scorecard, "/a", "L")


def test_markdown_blind_note_variants(tracks, results, scorecard):
    blind = Settings(blind=True)
    assert "Yes (revealed in this report)" in render_markdown(tracks, results, scorecard, "/a", "L", settings=blind)
    hidden = render_markdown(tracks, results, scorecard, "/a", "L", settings=blind, unblind=False)
    assert "- **Blind Mode**: True" in hidden


def test_markdown_no_rating_or_choice_criteria(tracks):
    sc = {"name": "Notes only", "criteria": normalize_criteria([{"key": "n", "type": "notes"}])}
    md = render_markdown(tracks, {"tracks": {}}, sc, "/a", "L")
    assert "_(No rating or choice criteria defined)_" in md
    assert "_(No notes recorded)_" in md


def test_markdown_choice_without_responses_and_rating_without_scores(tracks, scorecard):
    md = render_markdown(tracks, {"tracks": {}}, scorecard, "/a", "L")
    assert "_(No responses recorded)_" in md
    assert "### Metric:" not in md  # nothing scored -> no rating tables
    assert "_(No notes recorded)_" in md


def test_markdown_stats_rows_sorted_by_mean_descending(scorecard):
    tracks = [make_track("a/1.wav", "a", "Low"), make_track("b/1.wav", "b", "High")]
    res = {"tracks": {"a/1.wav": {"overall": 1}, "b/1.wav": {"overall": 5}}}
    md = render_markdown(tracks, res, scorecard, "/a", "L")
    assert md.index("| High |") < md.index("| Low |")


def test_markdown_root_level_track_uses_root_label(scorecard):
    t = make_track("solo.wav", group="", group_label="", label="Solo")
    md = render_markdown([t], {"tracks": {"solo.wav": {"overall": 3}}}, scorecard, "/a", "L")
    assert "| root | 3.00 | 0.00 | 1 |" in md
    assert "across" not in md  # no folder arms to mention


def test_markdown_unblind_false_uses_displayed_not_raw_fields(scorecard):
    t = make_track(group="blinded", group_label="Blind Evaluation", label="Track 01",
                   raw_group="arm1", raw_group_label="Secret Arm", raw_label="Secret Take")
    res = {"tracks": {"arm1/take1.wav": {"overall": 3, "notes": "n"}}}
    shown = render_markdown([t], res, scorecard, "/a", "L", unblind=False)
    assert "Blind Evaluation" in shown and "Track 01" in shown and "Secret" not in shown
    revealed = render_markdown([t], res, scorecard, "/a", "L", unblind=True)
    assert "Secret Arm" in revealed and "Secret Take" in revealed


def test_markdown_label_resolution_falls_back_through_stem_and_name(scorecard):
    bare = {"name": "x/y.wav", "stem": "y", "group": "x"}
    only_name = {"name": "z.wav"}
    md = render_markdown([bare, only_name], {"tracks": {}}, scorecard, "/a", "L")
    assert "| x | y |" in md and "| — | z.wav |" in md
    md2 = render_markdown([bare, only_name], {"tracks": {}}, scorecard, "/a", "L", unblind=False)
    assert "| x | y |" in md2 and "| — | z.wav |" in md2


def test_markdown_score_table_handles_none_values_and_missing_meta(scorecard):
    t = make_track(metadata={}, raw_metadata=None)
    res = {"tracks": {"arm1/take1.wav": {"overall": None, "notes": None}}}
    md = render_markdown([t], res, scorecard, "/a", "L", settings=Settings(report_columns=["seed"]))
    assert "Meta: seed" in md
    t2 = make_track(metadata={"seed": None})
    md2 = render_markdown([t2], res, scorecard, "/a", "L", settings=Settings(report_columns=["seed"]))
    assert "| Y |" not in md2 and "Meta: seed" in md2


def test_markdown_done_flag(tracks, results, scorecard):
    sc = {"name": "s", "criteria": normalize_criteria([{"key": "overall", "type": "rating"}])}
    assert "| Y |" in render_markdown(tracks, results, sc, "/a", "L")
    assert "| Y |" not in render_markdown(tracks, {"tracks": {}}, sc, "/a", "L")


# ------------------------------------------------------------------ csv
def parse_csv(text):
    return list(csv.reader(io.StringIO(text)))


def test_csv_full_row(tracks, results, scorecard):
    out = render_csv(tracks, results, scorecard, settings=Settings(report_columns=["seed", "model"]))
    header, row = parse_csv(out)
    assert header == ["file", "folder_arm", "track_label", "done", "overall", "fidelity",
                      "artifacts", "tags", "notes", "seed", "model"]
    assert row == ["arm1/take1.wav", "arm1", "Take 1", "1", "4", "5", "none", "x;y", "Crisp", "123", "test-v1"]


def test_csv_done_flag_and_blank_cells(scorecard):
    sc = {"criteria": normalize_criteria([{"key": "overall", "type": "rating"}])}
    t = make_track()
    done = parse_csv(render_csv([t], {"tracks": {"arm1/take1.wav": {"overall": 3}}}, sc))[1]
    assert done[3] == "1"
    missing = parse_csv(render_csv([t], {"tracks": {}}, sc))[1]
    assert missing[3] == "0" and missing[4] == ""
    none_val = parse_csv(render_csv([t], {"tracks": {"arm1/take1.wav": {"overall": None}}}, sc))[1]
    assert none_val[4] == ""


def test_csv_unblind_false_uses_displayed_fields(scorecard):
    t = make_track(group="blinded", label="Track 01", raw_group="arm1", raw_label="Secret")
    sc = {"criteria": normalize_criteria([{"key": "overall", "type": "rating"}])}
    hidden = parse_csv(render_csv([t], {"tracks": {}}, sc, unblind=False))[1]
    assert hidden[1:3] == ["blinded", "Track 01"]
    revealed = parse_csv(render_csv([t], {"tracks": {}}, sc))[1]
    assert revealed[1:3] == ["arm1", "Secret"]


def test_csv_meta_columns_tolerate_none_and_missing(scorecard):
    sc = {"criteria": normalize_criteria([{"key": "overall", "type": "rating"}])}
    t = make_track(metadata={"seed": None}, raw_metadata=None)
    row = parse_csv(render_csv([t], {"tracks": {}}, sc, settings=Settings(report_columns=["seed", "nope"])))[1]
    assert row[-2:] == ["", ""]


def test_csv_escapes_commas_and_quotes():
    sc = {"criteria": normalize_criteria([{"key": "notes", "type": "notes"}])}
    res = {"tracks": {"arm1/take1.wav": {"notes": 'say "hi", ok'}}}
    assert parse_csv(render_csv([make_track()], res, sc))[1][-1] == 'say "hi", ok'


def test_csv_label_falls_back_to_stem():
    t = {"name": "a.wav", "stem": "a"}
    sc = {"criteria": normalize_criteria([{"key": "overall", "type": "rating"}])}
    assert parse_csv(render_csv([t], {"tracks": {}}, sc))[1][:3] == ["a.wav", "", "a"]
    assert parse_csv(render_csv([t], {"tracks": {}}, sc, unblind=False))[1][:3] == ["a.wav", "", "a"]


# ------------------------------------------------------------------ json
def test_json_summary_statistics(results, scorecard):
    tracks = [make_track("a/1.wav"), make_track("a/2.wav"), make_track("a/3.wav")]
    res = {"tracks": {"a/1.wav": {"overall": 2}, "a/2.wav": {"overall": 4}, "a/3.wav": {"fidelity": "x"}}}
    out = render_json(tracks, res, scorecard, settings=Settings())
    assert out["scorecard"] == "Multi-Metric" and out["schema_version"] == 1
    assert out["summary"]["total_tracks"] == 3
    assert out["summary"]["ratings"]["overall"] == {"mean": 3.0, "std": 1.414, "n": 2}
    assert out["summary"]["ratings"]["fidelity"] == {"mean": 0.0, "std": 0.0, "n": 0}
    assert set(out["summary"]["ratings"]) == {"overall", "fidelity"}  # only rating questions
    assert out["tracks"] == res["tracks"]
    assert out["exported_at"].endswith("+00:00")
    json.dumps(out)  # must be serializable as-is


def test_json_accepts_flat_results(tracks, scorecard):
    out = render_json(tracks, {"arm1/take1.wav": {"overall": 5}}, scorecard)
    assert out["summary"]["ratings"]["overall"]["mean"] == 5.0
