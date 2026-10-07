"""rating_app.store - atomic writes, runs, snapshots, drift, legacy migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rating_app.store import Run, atomic_write_json, load_json, migrate_legacy_evaluations

SC = {"name": "Test", "criteria": [{"key": "overall", "type": "rating"}]}


# ------------------------------------------------------------------ atomic_write_json / load_json
def test_atomic_write_creates_parents_and_leaves_no_temp_files(tmp_path):
    target = tmp_path / "deep" / "er" / "data.json"
    atomic_write_json(target, {"é": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"é": 1}
    assert target.read_text(encoding="utf-8").endswith("\n")
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]


def test_atomic_write_failure_keeps_old_file_and_cleans_temp(tmp_path):
    target = tmp_path / "data.json"
    atomic_write_json(target, {"v": 1})
    with pytest.raises(TypeError):
        atomic_write_json(target, {"v": object()})  # not JSON serializable
    assert json.loads(target.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["data.json"]


def test_atomic_write_tolerates_temp_cleanup_failure(tmp_path, monkeypatch):
    target = tmp_path / "data.json"

    def failing_replace(self, dest):
        raise OSError("disk full")

    def failing_unlink(self, *a, **kw):
        raise OSError("busy")

    monkeypatch.setattr(Path, "replace", failing_replace)
    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(OSError, match="disk full"):  # original error wins, cleanup error swallowed
        atomic_write_json(target, {"v": 1})


def test_load_json_variants(tmp_path):
    assert load_json(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{nope")
    assert load_json(tmp_path / "bad.json") == {}
    (tmp_path / "list.json").write_text("[1]")
    assert load_json(tmp_path / "list.json") == {}
    (tmp_path / "ok.json").write_text('{"a": 1}')
    assert load_json(tmp_path / "ok.json") == {"a": 1}


# ------------------------------------------------------------------ legacy migration
def test_legacy_migration_wraps_old_format(tmp_path):
    legacy = tmp_path / "evaluations.json"
    legacy.write_text(json.dumps({"meta": {"label": "old"}, "tracks": {"t1.wav": {"overall": 3}}}))
    target = tmp_path / "new" / "results.json"
    assert migrate_legacy_evaluations(legacy, target) is True
    data = json.loads(target.read_text())
    assert data["tracks"]["t1.wav"]["overall"] == 3
    assert data["meta"] == {"label": "old"}
    assert data["schema_version"] == 1
    assert data["migrated_from"] == str(legacy.resolve())
    assert "migrated_at" in data


def test_legacy_migration_accepts_flat_track_mapping(tmp_path):
    legacy = tmp_path / "evaluations.json"
    legacy.write_text(json.dumps({"t1.wav": {"overall": 2}}))
    target = tmp_path / "results.json"
    assert migrate_legacy_evaluations(legacy, target)
    assert json.loads(target.read_text())["tracks"] == {"t1.wav": {"overall": 2}}


def test_legacy_migration_ignores_non_dict_tracks(tmp_path):
    legacy = tmp_path / "evaluations.json"
    legacy.write_text(json.dumps({"tracks": ["not", "a", "dict"]}))
    target = tmp_path / "results.json"
    assert migrate_legacy_evaluations(legacy, target)
    assert json.loads(target.read_text())["tracks"] == {}


@pytest.mark.parametrize("content", [None, "{}", "{broken", "[]"])
def test_legacy_migration_noop_for_missing_or_empty_source(tmp_path, content):
    legacy = tmp_path / "evaluations.json"
    if content is not None:
        legacy.write_text(content)
    target = tmp_path / "results.json"
    assert migrate_legacy_evaluations(legacy, target) is False
    assert not target.exists()


# ------------------------------------------------------------------ Run
def test_run_creation_persists_meta_and_snapshot(tmp_path):
    run = Run("run_001", tmp_path, audio="/dummy", label="Run One", scorecard=SC, settings_dict={"a": 1})
    assert run.run_meta_path.is_file() and run.snapshot_path.is_file()
    assert run.meta["label"] == "Run One" and run.meta["settings"] == {"a": 1}
    assert 10000 <= run.meta["blind_seed"] <= 99999
    assert run.scorecard_snapshot == SC
    assert run.tracks_data == {}


def test_run_label_defaults_to_run_id(tmp_path):
    assert Run("rid", tmp_path).meta["label"] == "rid"


def test_run_without_scorecard_has_empty_snapshot_and_no_file(tmp_path):
    run = Run("rid", tmp_path)
    assert run.scorecard_snapshot == {}
    assert not run.snapshot_path.exists()


def test_run_save_and_reload_round_trip(tmp_path):
    run = Run("run_001", tmp_path, audio="/x", label="L", scorecard=SC)
    run.set_track_evaluation("arm1/track1.wav", {"overall": 5, "notes": "Solid"})
    assert run.results_path.is_file()
    assert "updated" in run.get_track_evaluation("arm1/track1.wav")

    again = Run("run_001", tmp_path)  # reload from disk, no constructor args needed
    assert again.get_track_evaluation("arm1/track1.wav")["notes"] == "Solid"
    assert again.meta["blind_seed"] == run.meta["blind_seed"]  # seed is stable across reloads
    assert again.meta["label"] == "L"


def test_existing_snapshot_wins_over_new_scorecard(tmp_path):
    Run("r", tmp_path, scorecard=SC)
    other = {"name": "Changed", "criteria": [{"key": "zzz"}]}
    assert Run("r", tmp_path, scorecard=other).scorecard_snapshot == SC


def test_get_track_evaluation_unknown_track_is_empty(tmp_path):
    assert Run("r", tmp_path).get_track_evaluation("nope") == {}


def test_peek_meta_reads_without_creating_anything(tmp_path):
    assert Run.peek_meta(tmp_path, "ghost") == {}
    assert list(tmp_path.iterdir()) == []
    Run("real", tmp_path, audio="/a")
    assert Run.peek_meta(tmp_path, "real")["audio"] == "/a"


# ------------------------------------------------------------------ drift
def test_drift_detection_reports_orphaned_and_new_keys(tmp_path):
    run = Run("r", tmp_path, scorecard={"name": "Old", "criteria": [{"key": "overall"}, {"key": "mix"}]})
    drift = run.detect_drift({"name": "New", "criteria": [{"key": "overall"}, {"key": "melody"}]})
    assert drift == {
        "drifted": True,
        "orphaned_keys": ["mix"],
        "new_keys": ["melody"],
        "snapshot_name": "Old",
        "active_name": "New",
    }


def test_no_drift_when_keys_match(tmp_path):
    run = Run("r", tmp_path, scorecard=SC)
    assert run.detect_drift(SC)["drifted"] is False


def test_drift_without_snapshot_is_never_drifted(tmp_path):
    assert Run("r", tmp_path).detect_drift(SC) == {"drifted": False, "orphaned_keys": [], "new_keys": []}
