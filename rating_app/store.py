"""rating_app.store — runs management, scorecard snapshots, drift handling, and atomic writes."""

from __future__ import annotations

import os
import json
import random
import tempfile
import datetime as _dt
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON data to path using temporary file and os.replace."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(f".tmp.{os.getpid()}.{random.randint(1000, 9999)}")
    try:
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        temp_file.write_text(content, encoding="utf-8")
        temp_file.replace(path)
    finally:
        if temp_file.is_file():
            try:
                temp_file.unlink()
            except OSError:
                pass


def load_json(path: Path) -> dict[str, Any]:
    """Read a JSON file or return empty dict if missing or invalid."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def migrate_legacy_evaluations(legacy_path: Path, target_results_path: Path) -> bool:
    """Migrate legacy evaluations.json format into modern results.json format."""
    if not legacy_path.is_file():
        return False
    legacy_data = load_json(legacy_path)
    if not legacy_data:
        return False

    tracks = legacy_data.get("tracks", legacy_data)
    meta = legacy_data.get("meta", {})

    modern_results = {
        "schema_version": 1,
        "migrated_from": str(legacy_path.resolve()),
        "migrated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "meta": meta,
        "tracks": tracks if isinstance(tracks, dict) else {},
    }
    atomic_write_json(target_results_path, modern_results)
    return True


class Run:
    """Manages an isolated evaluation run directory."""

    def __init__(
        self,
        run_id: str,
        runs_dir: str | Path,
        audio: str = "",
        label: str = "",
        scorecard: dict | None = None,
        settings_dict: dict | None = None,
    ):
        self.run_id = run_id
        self.run_dir = Path(runs_dir).expanduser().resolve() / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self.run_meta_path = self.run_dir / "run.json"
        self.snapshot_path = self.run_dir / "scorecard.snapshot.json"
        self.results_path = self.run_dir / "results.json"

        # 1. Load or initialize run.json
        if self.run_meta_path.is_file():
            self.meta = load_json(self.run_meta_path)
        else:
            seed = random.randint(10000, 99999)
            self.meta = {
                "schema_version": 1,
                "run_id": run_id,
                "label": label or run_id,
                "audio": audio,
                "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "blind_seed": seed,
                "settings": settings_dict or {},
            }
            atomic_write_json(self.run_meta_path, self.meta)

        # 2. Scorecard snapshot
        if self.snapshot_path.is_file():
            self.scorecard_snapshot = load_json(self.snapshot_path)
        elif scorecard:
            self.scorecard_snapshot = dict(scorecard)
            atomic_write_json(self.snapshot_path, self.scorecard_snapshot)
        else:
            self.scorecard_snapshot = {}

        # 3. Results
        results_data = load_json(self.results_path)
        self.tracks_data: dict[str, dict[str, Any]] = results_data.get("tracks", {})

    @staticmethod
    def peek_meta(runs_dir: str | Path, run_id: str) -> dict[str, Any]:
        """Read an existing run's run.json without creating anything."""
        return load_json(Path(runs_dir).expanduser().resolve() / run_id / "run.json")

    def save(self) -> None:
        """Persist results and metadata atomically."""
        atomic_write_json(self.run_meta_path, self.meta)
        payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "updated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "tracks": self.tracks_data,
        }
        atomic_write_json(self.results_path, payload)

    def get_track_evaluation(self, track_name: str) -> dict[str, Any]:
        return self.tracks_data.get(track_name, {})

    def set_track_evaluation(self, track_name: str, eval_data: dict[str, Any]) -> None:
        eval_data["updated"] = _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        )
        self.tracks_data[track_name] = eval_data
        self.save()

    def detect_drift(self, active_scorecard: dict) -> dict[str, Any]:
        """Detect drift between frozen scorecard snapshot and active scorecard."""
        if not self.scorecard_snapshot:
            return {"drifted": False, "orphaned_keys": [], "new_keys": []}

        snapshot_keys = {c["key"] for c in self.scorecard_snapshot.get("criteria", [])}
        active_keys = {c["key"] for c in active_scorecard.get("criteria", [])}

        orphaned = sorted(snapshot_keys - active_keys)
        new_keys = sorted(active_keys - snapshot_keys)

        return {
            "drifted": bool(orphaned or new_keys),
            "orphaned_keys": orphaned,
            "new_keys": new_keys,
            "snapshot_name": self.scorecard_snapshot.get("name", "Unknown"),
            "active_name": active_scorecard.get("name", "Unknown"),
        }
