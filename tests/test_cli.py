"""rating_app.cli - argument parsing."""

from __future__ import annotations

import pytest

from rating_app.cli import get_parser, parse_args


def test_unset_options_are_dropped():
    """Unset options are dropped so they never mask lower-precedence settings layers.

    ``--out`` is the one exception today: it has a hard-coded argparse default, so it is
    always present (see the xfail test in test_settings.py for the consequence).
    """
    assert parse_args([]) == {"out": "./rating_out"}


def test_all_options_round_trip():
    got = parse_args(
        [
            "--audio", "/a", "--out", "/o", "--label", "L", "--scorecard", "sc",
            "--fields", "legacy.json", "--config", "cfg", "--run-id", "r1",
            "--host", "0.0.0.0", "--port", "8123", "--strict-port", "--blind", "--debug",
        ]
    )
    assert got == {
        "audio": "/a", "out": "/o", "label": "L", "scorecard": "sc",
        "fields": "legacy.json", "config": "cfg", "run_id": "r1",
        "host": "0.0.0.0", "port": 8123, "strict_port": True, "blind": True, "debug": True,
    }


def test_port_must_be_int(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--port", "abc"])
    assert exc.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_parse_args_reads_sys_argv_by_default(monkeypatch):
    monkeypatch.setattr("sys.argv", ["rating_app", "--label", "from-argv"])
    assert parse_args() == {"label": "from-argv", "out": "./rating_out"}


def test_parser_metadata():
    parser = get_parser()
    assert parser.prog == "rating_app"
    assert "--strict-port" in parser.format_help()
