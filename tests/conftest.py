"""Shared pytest fixtures.

Design rules
------------
* Every test is hermetic: no real HOME, no ``RATING_*`` env vars, and no writes into the
  repository's ``scorecards/`` / ``configs/`` / ``runs/`` folders (see ``_sandbox``).
* Audio fixtures are tiny real WAV files generated on the fly - nothing binary is committed.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import wave
from pathlib import Path

import pytest

import rating_app.paths as paths_mod
import rating_app.scorecard as scorecard_mod
import rating_app.settings as settings_mod
import rating_app.web as web_mod
from rating_app.settings import load_settings
from rating_app.web import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_SCORECARDS = REPO_ROOT / "scorecards"
REAL_CONFIGS = REPO_ROOT / "configs"

# Answers that make a track "done" under scorecards/default.json
DEFAULT_ANSWERS = {
    "overall": "4", "fidelity": "3", "coherence": "3",
    "mix": "3", "artifacts": "none", "keep": "yes",
}


# --------------------------------------------------------------------------- isolation
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path_factory):
    """No leaking of the developer's HOME or RATING_* variables into tests."""
    import os

    for name in list(os.environ):
        if name.startswith("RATING_"):
            monkeypatch.delenv(name)
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


@pytest.fixture(autouse=True)
def app_root(monkeypatch, tmp_path_factory) -> Path:
    """Point every module's notion of the app folder at a throw-away copy.

    The web UI can *write* scorecards, and ``runs/`` lives next to ``app.py``;
    sandboxing keeps the checkout pristine no matter what a test does.
    """
    root = tmp_path_factory.mktemp("approot")
    shutil.copytree(REAL_SCORECARDS, root / "scorecards")
    shutil.copytree(REAL_CONFIGS, root / "configs")
    monkeypatch.setattr(paths_mod, "APP_ROOT", root)
    monkeypatch.setattr(paths_mod, "SCORECARD_DIR", root / "scorecards")
    monkeypatch.setattr(paths_mod, "CONFIG_DIR", root / "configs")
    monkeypatch.setattr(scorecard_mod, "SCORECARD_DIR", root / "scorecards")
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", root / "configs")
    monkeypatch.setattr(web_mod, "SCORECARD_DIR", root / "scorecards")
    monkeypatch.setattr(web_mod, "APP_ROOT", root)
    return root


# --------------------------------------------------------------------------- audio
def write_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<50h", *([0] * 50)))
    return path


@pytest.fixture()
def wav():
    """Factory: ``wav(path)`` writes a tiny valid WAV file."""
    return write_wav


@pytest.fixture()
def audio(tmp_path) -> Path:
    """armA/{t1,t2}.wav + armB/t1.wav, with folder + track metadata on armA."""
    root = tmp_path / "audio"
    write_wav(root / "armA" / "t1.wav")
    write_wav(root / "armA" / "t2.wav")
    write_wav(root / "armB" / "t1.wav")
    (root / "armA" / "_meta.json").write_text(
        json.dumps({"label": "Prompt A", "prompt": "secret prompt"})
    )
    (root / "armA" / "t1.json").write_text(json.dumps({"seed": 7}))
    return root


# --------------------------------------------------------------------------- web client
@pytest.fixture()
def make_client(tmp_path, audio):
    """Factory returning ``(flask_test_client, settings)``; CLI-style overrides as kwargs."""

    def _make(**cli):
        args = {
            "audio": str(audio),
            "out": str(tmp_path / "out"),
            "runs_dir": str(tmp_path / "runs"),
            "label": "t",
        }
        args.update(cli)
        settings = load_settings(cli_args=args)
        return create_app(settings).test_client(), settings

    return _make


@pytest.fixture()
def track_links():
    """Helper: all ``/track/...`` links on the index page."""

    def _links(client):
        html = client.get("/").get_data(as_text=True)
        return re.findall(r'href="(/track/[^"]+)"', html)

    return _links


@pytest.fixture()
def full_scorecard(tmp_path) -> Path:
    """A scorecard that exercises every question type."""
    sc = {
        "name": "Everything",
        "summary_metric": "overall",
        "criteria": [
            {"key": "overall", "type": "rating", "max": 5, "scale_labels": {"1": "bad", "5": "great"}},
            {"key": "pick", "type": "choice", "options": ["a", "b"]},
            {"key": "tags", "type": "multi_choice", "options": [{"v": "x", "t": "Ex"}, "y"]},
            {"key": "ok", "type": "boolean"},
            {"key": "bpm", "type": "number"},
            {"key": "notes", "type": "notes"},
        ],
    }
    p = tmp_path / "everything.json"
    p.write_text(json.dumps(sc))
    return p
