"""rating_app.discovery - folder/track metadata, grouping, blinding, completeness."""

from __future__ import annotations

import json

import pytest

from rating_app.discovery import (
    discover,
    extract_folder_metadata,
    extract_track_metadata,
    is_done,
    load_file_labels,
    load_json_safe,
    sections_of,
)
from rating_app.scorecard import normalize_criteria


def jdump(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)


# ------------------------------------------------------------------ json helpers
def test_load_json_safe_variants(tmp_path):
    assert load_json_safe(tmp_path / "missing.json") == {}
    jdump(tmp_path / "bad.json", "{nope")
    assert load_json_safe(tmp_path / "bad.json") == {}
    jdump(tmp_path / "list.json", [1])
    assert load_json_safe(tmp_path / "list.json") == {}
    jdump(tmp_path / "ok.json", {"a": 1})
    assert load_json_safe(tmp_path / "ok.json") == {"a": 1}


def test_load_file_labels_stringifies_and_tolerates_absence(tmp_path):
    assert load_file_labels(tmp_path) == {}
    jdump(tmp_path / "labels.json", {"a.wav": 1, "b.wav": "Bee"})
    assert load_file_labels(tmp_path) == {"a.wav": "1", "b.wav": "Bee"}


# ------------------------------------------------------------------ extract_folder_metadata
@pytest.mark.parametrize("key", ["label", "name", "variant", "arm"])
def test_folder_label_keys(tmp_path, key):
    jdump(tmp_path / "_meta.json", {key: "  The Label ", "other": 1})
    label, meta = extract_folder_metadata(tmp_path)
    assert label == "The Label" and meta[key] == "  The Label "


@pytest.mark.parametrize(
    "data, expected",
    [
        ({"config": "cfgA", "extra_request_opts": "temp=1"}, "cfgA: temp=1"),
        ({"config": "cfgA", "opts": "temp=2"}, "cfgA: temp=2"),
        ({"opts": "only-opts"}, "only-opts"),
        ({"config": "only-cfg"}, "only-cfg"),
        ({"label": "   ", "config": "fallback"}, "fallback"),  # blank label ignored
        ({"unrelated": 1}, ""),
    ],
)
def test_folder_label_fallbacks_from_config_and_opts(tmp_path, data, expected):
    jdump(tmp_path / "_knob.json", data)
    assert extract_folder_metadata(tmp_path)[0] == expected


def test_folder_metadata_knob_wins_then_meta_is_consulted_if_no_label(tmp_path):
    jdump(tmp_path / "_knob.json", {"seed": 1})  # no usable label
    jdump(tmp_path / "_meta.json", {"label": "From meta", "model": "m"})
    label, meta = extract_folder_metadata(tmp_path)
    assert label == "From meta"
    assert meta == {"seed": 1, "label": "From meta", "model": "m"}


def test_folder_metadata_stops_after_first_sidecar_with_label(tmp_path):
    jdump(tmp_path / "_knob.json", {"label": "Knob"})
    jdump(tmp_path / "_meta.json", {"label": "Meta", "extra": 1})
    label, meta = extract_folder_metadata(tmp_path)
    assert label == "Knob" and "extra" not in meta


def test_folder_metadata_empty_folder(tmp_path):
    assert extract_folder_metadata(tmp_path) == ("", {})


# ------------------------------------------------------------------ extract_track_metadata
def test_track_metadata_from_sidecar(tmp_path):
    wav = tmp_path / "take.wav"
    wav.write_bytes(b"x")
    assert extract_track_metadata(wav) == {}
    jdump(tmp_path / "take.json", {"seed": 5})
    assert extract_track_metadata(wav) == {"seed": 5}


# ------------------------------------------------------------------ discover
def test_discover_none_or_missing_dir_is_empty(tmp_path):
    assert discover(None) == []
    assert discover("") == []
    assert discover(tmp_path / "nope") == []


def test_discover_groups_labels_and_filters_extensions(tmp_path):
    arm = tmp_path / "arm1"
    arm.mkdir()
    (arm / "take1.wav").write_bytes(b"x")
    (arm / "take2.MP3").write_bytes(b"x")
    (arm / "ignore.txt").write_text("text")
    jdump(arm / "_knob.json", {"label": "Arm 1 Label"})
    jdump(tmp_path / "labels.json", {"arm1/take1.wav": "Custom Take 1", "take2.MP3": "By filename"})

    tracks = discover(tmp_path)
    assert [t["name"] for t in tracks] == ["arm1/take1.wav", "arm1/take2.MP3"]
    first, second = tracks
    assert first["group"] == "arm1" and first["group_label"] == "Arm 1 Label"
    assert first["label"] == "Custom Take 1"
    assert second["label"] == "By filename"  # falls back to bare-filename label key
    assert first["id"] == first["name"] == first["file"]


def test_discover_custom_extensions(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "b.aiff").write_bytes(b"x")
    assert [t["name"] for t in discover(tmp_path, audio_exts=[".AIFF"])] == ["b.aiff"]


def test_discover_root_level_files_have_no_group(tmp_path):
    (tmp_path / "solo.wav").write_bytes(b"x")
    (t,) = discover(tmp_path)
    assert t["group"] == "" and t["group_label"] == "" and t["label"] == "solo"


def test_discover_group_by_none_and_filename(tmp_path):
    (tmp_path / "armA").mkdir()
    (tmp_path / "armA" / "x_one.wav").write_bytes(b"x")
    (tmp_path / "plain.wav").write_bytes(b"x")
    assert {t["group"] for t in discover(tmp_path, group_by="none")} == {""}
    by_name = {t["name"]: t["group"] for t in discover(tmp_path, group_by="filename")}
    assert by_name == {"armA/x_one.wav": "x", "plain.wav": ""}


def test_discover_merges_folder_and_track_metadata_track_wins(tmp_path):
    arm = tmp_path / "prompt_a"
    arm.mkdir()
    jdump(arm / "_meta.json", {"label": "Prompt A", "model": "vocal-large-1", "seed": 0})
    (arm / "take_01.wav").write_bytes(b"x")
    jdump(arm / "take_01.json", {"seed": 1234, "cfg": 7.5})
    (t,) = discover(tmp_path)
    assert t["metadata"] == {"label": "Prompt A", "model": "vocal-large-1", "seed": 1234, "cfg": 7.5}
    assert t["group_label"] == "Prompt A"


def test_discover_blind_hides_identity_but_keeps_raw_fields(tmp_path):
    arm = tmp_path / "prompt_a"
    arm.mkdir()
    jdump(arm / "_meta.json", {"label": "Prompt A", "prompt": "sing"})
    (arm / "take_01.wav").write_bytes(b"x")
    (t,) = discover(tmp_path, blind=True, blind_seed=99)
    assert t["id"] == "b01" and t["label"] == "Track 01"
    assert t["group"] == "blinded" and t["group_label"] == "Blind Evaluation"
    assert t["metadata"] == {}
    assert t["raw_group"] == "prompt_a" and t["raw_group_label"] == "Prompt A"
    assert t["raw_metadata"]["prompt"] == "sing"
    assert t["name"] == "prompt_a/take_01.wav"  # needed to key results and serve audio


def test_discover_blind_order_depends_only_on_seed(tmp_path):
    for arm in "abc":
        (tmp_path / arm).mkdir()
        (tmp_path / arm / "t.wav").write_bytes(b"x")

    def order(seed):
        return [t["name"] for t in discover(tmp_path, blind=True, blind_seed=seed)]

    assert order(1) == order(1)
    assert any(order(1) != order(s) for s in range(2, 12))
    assert len(order(None)) == 3  # default seed path


# ------------------------------------------------------------------ sections_of
def test_sections_of_preserves_order_and_labels():
    tracks = [
        {"group": "b", "group_label": "Bee"},
        {"group": "a", "group_label": ""},
        {"group": "b", "group_label": "Bee"},
        {"group": "", "group_label": ""},
    ]
    sections = sections_of(tracks)
    assert [label for label, _ in sections] == ["Bee", "a", "Tracks"]
    assert [len(items) for _, items in sections] == [2, 1, 1]


def test_sections_of_empty():
    assert sections_of([]) == []


# ------------------------------------------------------------------ is_done
def test_is_done_basic_required_vs_optional():
    criteria = normalize_criteria([{"key": "q1", "type": "rating"}, {"key": "notes", "type": "notes"}])
    assert is_done(None, criteria) is False
    assert is_done({}, criteria) is False
    assert is_done({"q1": ""}, criteria) is False
    assert is_done({"q1": "  "}, criteria) is False
    assert is_done({"q1": "4"}, criteria) is True
    assert is_done({"q1": "4", "notes": ""}, criteria) is True


def test_is_done_hidden_required_question_does_not_block():
    crit = [
        {"key": "a", "type": "rating", "required": True},
        {"key": "why", "type": "choice", "required": True, "show_if": {"key": "a", "lte": 2}},
    ]
    assert is_done({"a": "5"}, crit) is True
    assert is_done({"a": "1"}, crit) is False
    assert is_done({"a": "1", "why": "x"}, crit) is True


def test_is_done_required_multi_choice_rejects_empty_selection():
    crit = [{"key": "m", "type": "multi_choice", "required": True}]
    assert is_done({"m": []}, crit) is False
    assert is_done({"m": ["x"]}, crit) is True
