"""rating_app.settings - layered configuration with source tracking."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rating_app.cli import parse_args
from rating_app.settings import Settings, _parse_bool, _read_json_file, load_settings


def write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


# ------------------------------------------------------------------ helpers
@pytest.mark.parametrize("val", [True, "1", "true", "TRUE", " yes ", "on"])
def test_parse_bool_truthy(val):
    assert _parse_bool(val) is True


@pytest.mark.parametrize("val", [False, "0", "false", "no", "off", "", "maybe"])
def test_parse_bool_falsy(val):
    assert _parse_bool(val) is False


def test_read_json_file_variants(tmp_path):
    assert _read_json_file(tmp_path / "missing.json") == {}
    assert _read_json_file(write_json(tmp_path / "bad.json", "{nope")) == {}
    assert _read_json_file(write_json(tmp_path / "list.json", [1, 2])) == {}
    assert _read_json_file(write_json(tmp_path / "ok.json", {"a": 1})) == {"a": 1}


def test_settings_serialization_round_trips():
    s = Settings(label="x", report_columns=["seed"])
    assert json.loads(s.to_json())["label"] == "x"
    assert s.to_dict()["report_columns"] == ["seed"]


# ------------------------------------------------------------------ defaults
def test_defaults():
    s = load_settings()
    assert (s.host, s.port, s.blind) == ("127.0.0.1", 5000, False)
    assert s.sources["host"] == "default"
    assert "sources" not in s.sources


# ------------------------------------------------------------------ precedence
def test_env_overrides_config_file_and_cli_overrides_env(tmp_path, monkeypatch):
    cfg = write_json(tmp_path / "custom.json", {"port": 6000, "blind": True})
    monkeypatch.setenv("RATING_PORT", "7000")

    s1 = load_settings(config_file_override=str(cfg))
    assert s1.port == 7000 and s1.blind is True
    assert "env" in s1.sources["port"]
    assert s1.sources["blind"].startswith("config file")

    s2 = load_settings(cli_args={"port": 8000}, config_file_override=str(cfg))
    assert s2.port == 8000
    assert s2.sources["port"] == "CLI argument"


def test_user_config_is_lowest_priority_layer(tmp_path):
    user_conf = Path.home() / ".config" / "rating_app" / "config.json"
    write_json(user_conf, {"port": 1111, "host": "10.0.0.1"})
    write_json(tmp_path / "cfg.json", {"port": 2222})
    s = load_settings(config_file_override=str(tmp_path / "cfg.json"))
    assert s.host == "10.0.0.1" and s.sources["host"] == str(user_conf)
    assert s.port == 2222


def test_audio_folder_rating_json_layer(tmp_path):
    write_json(tmp_path / "_rating.json", {"label": "audio_specific_label"})
    s = load_settings(audio_path_override=str(tmp_path))
    assert s.label == "audio_specific_label"
    assert "_rating.json" in s.sources["label"]


def test_audio_from_cli_and_env_also_locate_rating_json(tmp_path, monkeypatch):
    write_json(tmp_path / "_rating.json", {"port": 4321})
    assert load_settings(cli_args={"audio": str(tmp_path)}).port == 4321
    monkeypatch.setenv("RATING_AUDIO", str(tmp_path))
    assert load_settings().port == 4321


def test_deprecated_fields_alias_maps_to_scorecard():
    s = load_settings(cli_args={"fields": "custom_scorecard.json"})
    assert s.scorecard == "custom_scorecard.json"
    assert s.sources["scorecard"] == "CLI argument"


def test_none_cli_values_are_ignored():
    assert load_settings(cli_args={"port": None, "host": None}).port == 5000


# ------------------------------------------------------------------ value coercion
def test_values_are_coerced_to_declared_types(monkeypatch):
    monkeypatch.setenv("RATING_BLIND", "yes")
    monkeypatch.setenv("RATING_PORT", "9001")
    monkeypatch.setenv("RATING_REPORT_COLUMNS", "seed, model ,, cfg")
    s = load_settings()
    assert s.blind is True
    assert s.port == 9001
    assert s.report_columns == ["seed", "model", "cfg"]


def test_invalid_int_is_skipped_and_keeps_previous_value(monkeypatch):
    monkeypatch.setenv("RATING_PORT", "not-a-number")
    s = load_settings()
    assert s.port == 5000 and s.sources["port"] == "default"


def test_null_values_in_config_file_are_ignored(tmp_path):
    cfg = write_json(tmp_path / "c.json", {"port": None, "label": "kept"})
    s = load_settings(config_file_override=str(cfg))
    assert s.port == 5000 and s.sources["port"] == "default" and s.label == "kept"


def test_unknown_keys_are_ignored(tmp_path):
    cfg = write_json(tmp_path / "c.json", {"totally_unknown": 1, "label": "ok"})
    s = load_settings(config_file_override=str(cfg))
    assert s.label == "ok" and not hasattr(s, "totally_unknown")


# ------------------------------------------------------------------ explicit config file
def test_config_by_preset_name(app_root):
    s = load_settings(cli_args={"config": "blind_eval"})
    assert s.blind is True
    assert "blind_eval.json" in s.sources["blind"]


def test_config_from_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("RATING_CONFIG", str(write_json(tmp_path / "e.json", {"port": 3333})))
    assert load_settings().port == 3333


def test_missing_config_file_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_settings(cli_args={"config": str(tmp_path / "missing.json")})


def test_malformed_config_file_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="Cannot read config file"):
        load_settings(cli_args={"config": str(write_json(tmp_path / "bad.json", "{not json"))})


def test_non_object_config_file_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_settings(cli_args={"config": str(write_json(tmp_path / "list.json", [1]))})


# ------------------------------------------------------------------ label inference
def test_label_inferred_from_audio_folder(tmp_path):
    folder = tmp_path / "my_session"
    folder.mkdir()
    s = load_settings(cli_args={"audio": str(folder)})
    assert s.label == "my_session"
    assert s.sources["label"].startswith("inferred from audio folder")


def test_explicit_label_is_not_overwritten(tmp_path):
    s = load_settings(cli_args={"audio": str(tmp_path), "label": "mine"})
    assert s.label == "mine" and s.sources["label"] == "CLI argument"


def test_blank_explicit_label_is_replaced_by_folder_name(tmp_path):
    """Pins current behaviour: a blank label counts as 'unset' even when it came from the CLI."""
    s = load_settings(cli_args={"audio": str(tmp_path), "label": ""})
    assert s.label == tmp_path.name
    assert s.sources["label"] == "CLI argument"  # source is not updated (cosmetic quirk)


def test_label_kept_default_without_audio():
    assert load_settings().label == "listening"


# ------------------------------------------------------------------ known bugs (documented)
@pytest.mark.xfail(strict=True, reason="BUG: --out has an argparse default, so it is always a "
                   "'CLI argument' and masks RATING_OUT / config-file values (README says CLI > env).")
def test_cli_default_for_out_must_not_mask_env(monkeypatch):
    monkeypatch.setenv("RATING_OUT", "/from/env")
    assert load_settings(cli_args=parse_args([])).out == "/from/env"


@pytest.mark.xfail(strict=True, reason="BUG: Settings has no `run_id` field, so --run-id is "
                   "silently dropped (web.create_app reads getattr(settings, 'run_id', None)).")
def test_run_id_flag_reaches_settings():
    assert getattr(load_settings(cli_args=parse_args(["--run-id", "r1"])), "run_id", None) == "r1"
