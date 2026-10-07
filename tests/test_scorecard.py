"""rating_app.scorecard - validation, normalization, loading, library listing."""

from __future__ import annotations

import json

import pytest

import rating_app.scorecard as sc
from conftest import REAL_SCORECARDS
from rating_app.scorecard import (
    list_scorecards,
    load_scorecard,
    normalize_criteria,
    normalize_scorecard,
    norm_options,
    show_if_met,
    validate_scorecard,
)

SHIPPED = sorted(REAL_SCORECARDS.glob("*.json"))


# ------------------------------------------------------------------ shipped scorecards
@pytest.mark.parametrize("path", SHIPPED, ids=lambda p: p.stem)
def test_every_shipped_scorecard_is_valid(path):
    card = load_scorecard(path)
    assert card["criteria"]
    assert card["summary_metric"] in {c["key"] for c in card["criteria"] if c["type"] == "rating"}


def test_load_default_by_path():
    card = load_scorecard(REAL_SCORECARDS / "default.json")
    assert card["summary_metric"] == "overall"
    assert card["schema_version"] == 1
    assert len(card["criteria"]) >= 5
    assert card["path"].endswith("default.json")


def test_load_arabic_vocal_has_expected_questions():
    keys = [c["key"] for c in load_scorecard(REAL_SCORECARDS / "arabic_vocal.json")["criteria"]]
    assert {"melody", "prosody"} <= set(keys)


# ------------------------------------------------------------------ norm_options
def test_norm_options_handles_strings_and_dicts():
    out = norm_options(["a", {"v": "b", "t": "Bee"}, {"value": "c", "label": "Sea"}, {"v": "d"}])
    assert out == [
        {"v": "a", "t": "a"},
        {"v": "b", "t": "Bee"},
        {"v": "c", "t": "Sea"},
        {"v": "d", "t": "d"},
    ]


def test_norm_options_none_is_empty():
    assert norm_options(None) == []


# ------------------------------------------------------------------ validate_scorecard
@pytest.mark.parametrize(
    "data, fragment",
    [
        ("nope", "JSON object or list"),
        ({"criteria": []}, "non-empty"),
        ({"criteria": "x"}, "non-empty"),
        ({}, "non-empty"),
        ({"schema_version": "1", "criteria": [{"key": "a"}]}, "schema_version"),
        ({"criteria": ["not-a-dict"]}, "must be an object"),
        ({"criteria": [{"type": "rating"}]}, "missing required field `key`"),
        ({"criteria": [{"key": "d"}, {"key": "d"}]}, "Duplicate"),
        ({"criteria": [{"key": "a", "type": "banana"}]}, "unsupported type"),
        ({"criteria": [{"key": "a", "max": 1}]}, "max"),
        ({"criteria": [{"key": "a", "max": "5"}]}, "max"),
        ({"criteria": [{"key": "a", "scale_labels": "bad"}]}, "scale_labels"),
        ({"criteria": [{"key": "a", "type": "choice", "options": []}]}, "options"),
        ({"criteria": [{"key": "a", "type": "radio"}]}, "options"),
        ({"criteria": [{"key": "a", "show_if": "x"}]}, "show_if"),
        ({"criteria": [{"key": "a", "show_if": {"eq": 1}}]}, "show_if"),
        ({"summary_metric": "ghost", "criteria": [{"key": "a"}]}, "does not match"),
        (
            {"summary_metric": "n", "criteria": [{"key": "n", "type": "notes"}]},
            "must refer to a criterion of type 'rating'",
        ),
    ],
)
def test_validate_scorecard_rejects(data, fragment):
    errors = validate_scorecard(data)
    assert any(fragment in e for e in errors), errors


@pytest.mark.parametrize(
    "data",
    [
        [{"key": "a"}],  # bare list is allowed
        {"schema_version": 1, "summary_metric": "a", "criteria": [{"key": "a", "type": "rating", "max": 7}]},
        {"criteria": [{"key": "a", "scale_labels": ["x", "y"]}, {"key": "b", "scale_labels": {"1": "x"}}]},
        {"criteria": [{"key": "a", "type": "boolean"}, {"key": "n", "type": "number"}]},
        {"criteria": [{"key": "a", "type": "multi_choice", "options": ["x"]}]},
        {"criteria": [{"key": "a"}, {"key": "b", "show_if": {"key": "a", "lte": 2}}]},
    ],
)
def test_validate_scorecard_accepts(data):
    assert validate_scorecard(data) == []


# ------------------------------------------------------------------ show_if_met
@pytest.mark.parametrize(
    "cond, rec, expected",
    [
        (None, {}, True),                                  # no condition
        ({"key": "a", "eq": 1}, None, True),               # controlling question unanswered
        ({"key": "a", "eq": 1}, {"a": ""}, True),
        ({"key": "a", "eq": "x"}, {"a": "x"}, True),
        ({"key": "a", "eq": "x"}, {"a": "y"}, False),
        ({"key": "a", "neq": "x"}, {"a": "y"}, True),
        ({"key": "a", "neq": "x"}, {"a": "x"}, False),
        ({"key": "a", "lt": 3}, {"a": "2"}, True),
        ({"key": "a", "lt": 3}, {"a": "3"}, False),
        ({"key": "a", "lte": 3}, {"a": 3}, True),
        ({"key": "a", "gt": 3}, {"a": "4"}, True),
        ({"key": "a", "gt": 3}, {"a": "3"}, False),
        ({"key": "a", "gte": 3}, {"a": "3"}, True),
        ({"key": "a", "gte": 3}, {"a": "2.5"}, False),
        ({"key": "a", "gt": 3}, {"a": "not-a-number"}, False),  # numeric op on text
        ({"key": "a", "gte": 1, "lte": 3}, {"a": "2"}, True),   # ops are AND-ed
        ({"key": "a", "gte": 1, "lte": 3}, {"a": "9"}, False),
    ],
)
def test_show_if_met(cond, rec, expected):
    crit = {"key": "q"} if cond is None else {"key": "q", "show_if": cond}
    assert show_if_met(crit, rec) is expected


# ------------------------------------------------------------------ normalize_*
def test_normalize_criteria_defaults():
    norm = normalize_criteria(
        [
            {"key": "test_rating"},
            {"key": "test_choice", "type": "choice", "options": ["a", "b"]},
            {"key": "test_notes", "type": "notes"},
        ]
    )
    assert [c["type"] for c in norm] == ["rating", "choice", "notes"]
    assert norm[0]["max"] == 5 and norm[0]["required"] is True
    assert norm[0]["label"] == "test_rating" and norm[0]["help"] == ""
    assert norm[1]["options"] == [{"v": "a", "t": "a"}, {"v": "b", "t": "b"}]
    assert norm[2]["required"] is False


def test_normalize_criteria_does_not_mutate_input():
    raw = [{"key": "a"}]
    normalize_criteria(raw)
    assert raw == [{"key": "a"}]


def test_normalize_criteria_radio_becomes_choice_and_multi_is_optional():
    radio, multi = normalize_criteria(
        [{"key": "r", "type": "RADIO", "options": ["x"]}, {"key": "m", "type": "multi_choice", "options": ["x"]}]
    )
    assert radio["type"] == "choice"
    assert multi["required"] is False


def test_normalize_criteria_scale_labels_list_and_dict():
    from_list, from_dict = normalize_criteria(
        [{"key": "a", "scale_labels": ["bad", "ok"]}, {"key": "b", "scale_labels": {1: "bad"}}]
    )
    assert from_list["scale_labels"] == {"1": "bad", "2": "ok"}
    assert from_dict["scale_labels"] == {"1": "bad"}


def test_normalize_criteria_boolean_gets_yes_no():
    (b,) = normalize_criteria([{"key": "b", "type": "boolean"}])
    assert b["options"] == [{"v": "true", "t": "Yes"}, {"v": "false", "t": "No"}]


def test_normalize_scorecard_from_list_picks_first_rating_as_summary():
    card = normalize_scorecard([{"key": "n", "type": "notes"}, {"key": "r"}])
    assert card["summary_metric"] == "r"
    assert card["name"] == "Scorecard" and card["sections"] == []


def test_normalize_scorecard_replaces_bad_summary_metric():
    card = normalize_scorecard({"summary_metric": "ghost", "criteria": [{"key": "r"}]})
    assert card["summary_metric"] == "r"


def test_normalize_scorecard_without_rating_has_no_summary():
    assert normalize_scorecard({"criteria": [{"key": "n", "type": "notes"}]})["summary_metric"] is None


def test_normalize_scorecard_empty_uses_fallback_criteria():
    assert [c["key"] for c in normalize_scorecard({})["criteria"]] == ["overall", "notes"]


# ------------------------------------------------------------------ load_scorecard
def test_load_scorecard_none_uses_default_independent_of_cwd(tmp_path, monkeypatch):
    """Regression: from another cwd the default silently became a 2-question fallback."""
    monkeypatch.chdir(tmp_path)
    assert len(load_scorecard(None)["criteria"]) >= 6


def test_load_scorecard_none_without_default_file_warns_and_falls_back(app_root):
    (app_root / "scorecards" / "default.json").unlink()
    with pytest.warns(UserWarning, match="minimal built-in fallback"):
        card = load_scorecard(None)
    assert [c["key"] for c in card["criteria"]] == ["overall", "notes"]


def test_load_scorecard_none_without_fallback_raises():
    with pytest.raises(ValueError, match="No scorecard provided"):
        load_scorecard(None, fallback_default=False)


def test_load_scorecard_from_dict_and_list():
    assert load_scorecard({"criteria": [{"key": "a"}]})["criteria"][0]["key"] == "a"
    assert load_scorecard([{"key": "b"}])["criteria"][0]["key"] == "b"


@pytest.mark.parametrize("bad", [{"criteria": []}, [{"type": "rating"}]])
def test_load_scorecard_invalid_inline_raises(bad):
    with pytest.raises(ValueError, match="validation failed"):
        load_scorecard(bad)


def test_load_scorecard_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        load_scorecard(tmp_path / "nope.json")


def test_load_scorecard_bad_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_scorecard(p)


def test_load_scorecard_unreadable_file(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    p.write_text("{}")

    def boom(self, *a, **kw):
        raise PermissionError("denied")

    monkeypatch.setattr(type(p), "read_text", boom)
    with pytest.raises(ValueError, match="Cannot read"):
        load_scorecard(p)


def test_load_scorecard_file_failing_validation(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"criteria": []}))
    with pytest.raises(ValueError, match="validation failed for"):
        load_scorecard(p)


# ------------------------------------------------------------------ list_scorecards
def test_list_scorecards_finds_shipped_cards():
    cards = list_scorecards([REAL_SCORECARDS])
    ids = {c["id"] for c in cards}
    assert {"default", "arabic_vocal"} <= ids
    default = next(c for c in cards if c["id"] == "default")
    assert default["num_criteria"] >= 5 and default["summary_metric"] == "overall"


def test_list_scorecards_skips_missing_dirs_dupes_and_invalid(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"name": "G", "criteria": [{"key": "a"}]}))
    (tmp_path / "broken.json").write_text("{nope")
    (tmp_path / "invalid.json").write_text(json.dumps({"criteria": []}))
    cards = list_scorecards([tmp_path / "does-not-exist", tmp_path, tmp_path])
    assert [c["id"] for c in cards] == ["good"]
    assert cards[0]["name"] == "G"


def test_module_constants_are_sane():
    assert "rating" in sc.SUPPORTED_TYPES and "radio" in sc.SUPPORTED_TYPES
    assert validate_scorecard(sc.DEFAULT_SCORECARD) == []
