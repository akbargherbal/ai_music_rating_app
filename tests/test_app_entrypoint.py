"""app.py - the thin CLI entry point (argument parsing -> settings -> create_app -> run)."""

from __future__ import annotations

import io
import runpy
import sys
from pathlib import Path

import pytest
import flask

import app as entry


class FakeFlask:
    """Stands in for the Flask app so main() never starts a real server."""

    def __init__(self):
        self.ran_with = None

    def run(self, **kwargs):
        self.ran_with = kwargs


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture()
def fake_app(monkeypatch):
    fake = FakeFlask()
    created = {}

    def fake_create_app(settings):
        created["settings"] = settings
        return fake

    monkeypatch.setattr(entry, "create_app", fake_create_app)
    monkeypatch.setattr(entry, "pick_free_port", lambda host, port: port)
    fake.created = created
    return fake


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["app.py", *argv])
    return entry.main()


def test_main_happy_path_prints_url_and_runs_server(monkeypatch, fake_app, audio, tmp_path, capsys):
    code = run_main(monkeypatch, "--audio", str(audio), "--out", str(tmp_path / "o"),
                    "--label", "demo", "--port", "5123", "--host", "127.0.0.1", "--debug")
    out = capsys.readouterr().out
    assert code == 0
    assert "ready for 'demo' [scorecard: default]" in out
    assert "open http://127.0.0.1:5123" in out
    assert fake_app.ran_with == {"host": "127.0.0.1", "port": 5123, "debug": True}


def test_main_reports_when_preferred_port_was_busy(monkeypatch, fake_app, audio, capsys):
    monkeypatch.setattr(entry, "pick_free_port", lambda host, port: port + 7)
    run_main(monkeypatch, "--audio", str(audio), "--port", "5000")
    out = capsys.readouterr().out
    assert "(port 5000 busy; using 5007)" in out and "http://127.0.0.1:5007" in out
    assert fake_app.ran_with["port"] == 5007


def test_main_strict_port_skips_scanning(monkeypatch, fake_app, audio, capsys):
    monkeypatch.setattr(entry, "pick_free_port", lambda *a: pytest.fail("must not scan"))
    run_main(monkeypatch, "--audio", str(audio), "--port", "5000", "--strict-port")
    assert fake_app.ran_with["port"] == 5000
    assert "busy" not in capsys.readouterr().out


def test_main_settings_error_returns_2(monkeypatch, fake_app, tmp_path, capsys):
    code = run_main(monkeypatch, "--config", str(tmp_path / "missing.json"))
    assert code == 2
    assert "rating_app: Config file not found" in capsys.readouterr().err
    assert fake_app.ran_with is None


def test_main_create_app_error_returns_2(monkeypatch, audio, capsys):
    def boom(settings):
        raise FileNotFoundError("Scorecard not found: x")

    monkeypatch.setattr(entry, "create_app", boom)
    assert run_main(monkeypatch, "--audio", str(audio)) == 2
    assert "Scorecard not found: x" in capsys.readouterr().err


def test_main_warns_about_missing_audio_folder_but_still_starts(monkeypatch, fake_app, tmp_path, capsys):
    code = run_main(monkeypatch, "--audio", str(tmp_path / "later"))
    assert code == 0
    assert "warning — folder not found yet" in capsys.readouterr().out
    assert fake_app.ran_with is not None


def test_main_non_interactive_without_audio_does_not_prompt(monkeypatch, fake_app):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("must not prompt"))
    assert run_main(monkeypatch) == 0
    assert fake_app.created["settings"].audio == ""


def test_main_prompts_on_a_terminal_and_infers_label(monkeypatch, fake_app, audio):
    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt: f'  "{audio}"  ')
    assert run_main(monkeypatch) == 0
    s = fake_app.created["settings"]
    assert s.audio == str(audio)
    assert s.label == audio.name
    assert s.sources["audio"] == "interactive terminal prompt"


def test_main_prompt_keeps_explicit_label(monkeypatch, fake_app, audio):
    monkeypatch.setattr(sys, "stdin", FakeTTY())
    monkeypatch.setattr("builtins.input", lambda prompt: str(audio))
    run_main(monkeypatch, "--label", "mine")
    assert fake_app.created["settings"].label == "mine"


@pytest.mark.parametrize("behaviour", ["blank", "eof"])
def test_main_prompt_blank_or_eof_leaves_audio_unset(monkeypatch, fake_app, behaviour):
    monkeypatch.setattr(sys, "stdin", FakeTTY())

    def fake_input(prompt):
        if behaviour == "eof":
            raise EOFError
        return "   "

    monkeypatch.setattr("builtins.input", fake_input)
    assert run_main(monkeypatch) == 0
    assert fake_app.created["settings"].audio == ""


def test_script_entry_point_bootstraps_sys_path_and_exits_cleanly(monkeypatch, audio, tmp_path):
    """`python app.py ...` must work even when the repo root is not already importable."""
    root = Path(entry.__file__).resolve().parent
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p or ".").resolve() != root])
    ran = {}
    monkeypatch.setattr(flask.Flask, "run", lambda self, **kw: ran.update(kw))
    monkeypatch.setattr(sys, "argv", ["app.py", "--audio", str(audio), "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(root / "app.py"), run_name="__main__")
    assert exc.value.code == 0
    assert ran["host"] == "127.0.0.1"
    assert str(root) in sys.path  # inserted by the bootstrap


def test_import_does_not_start_anything():
    """Importing app.py must be side-effect free (guarded by __name__ == '__main__')."""
    assert callable(entry.main)
