"""Route-level tests (Flask test client). These pin the regressions found in review:
track-page crash with metadata, blind-mode leaks, cwd-dependence, silent failures."""

import json
import os
import re
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from rating_app.paths import APP_ROOT
from rating_app.scorecard import load_scorecard
from rating_app.settings import load_settings
from rating_app.web import create_app


def _wav(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<50h", *([0] * 50)))


@pytest.fixture()
def audio(tmp_path):
    root = tmp_path / "audio"
    _wav(root / "armA" / "t1.wav")
    _wav(root / "armA" / "t2.wav")
    _wav(root / "armB" / "t1.wav")
    (root / "armA" / "_meta.json").write_text(json.dumps({"label": "Prompt A", "prompt": "secret prompt"}))
    (root / "armA" / "t1.json").write_text(json.dumps({"seed": 7}))
    return root


def make_client(tmp_path, audio, **cli):
    args = {"audio": str(audio), "out": str(tmp_path / "out"),
            "runs_dir": str(tmp_path / "runs"), "label": "t"}
    args.update(cli)
    s = load_settings(cli_args=args)
    return create_app(s).test_client(), s


def track_links(client):
    return re.findall(r'href="(/track/[^"]+)"', client.get("/").get_data(as_text=True))


def test_all_pages_ok(tmp_path, audio):
    c, _ = make_client(tmp_path, audio)
    for url in ["/", "/setup", "/criteria", "/settings", "/report", "/report.md", "/report.csv", "/report.json"]:
        assert c.get(url).status_code == 200, url


def test_track_page_with_metadata_renders(tmp_path, audio):
    """Regression: `% project %` typo made every track with metadata return 500."""
    c, _ = make_client(tmp_path, audio)
    links = track_links(c)
    assert len(links) == 3
    for l in links:
        assert c.get(l).status_code == 200, l
    page = c.get("/track/armA/t1.wav").get_data(as_text=True)
    assert "secret prompt" in page and "seed" in page


def test_save_and_report_roundtrip(tmp_path, audio):
    c, s = make_client(tmp_path, audio)
    r = c.post("/track/armB/t1.wav", data={"overall": "4", "fidelity": "3", "coherence": "3",
                                           "mix": "3", "artifacts": "none", "keep": "yes"})
    assert r.status_code == 302
    results = json.loads(next((tmp_path / "runs").glob("*/results.json")).read_text())
    assert results["tracks"]["armB/t1.wav"]["overall"] == "4"
    assert "armB" in c.get("/report.md").get_data(as_text=True)


def test_blind_mode_does_not_leak_arm(tmp_path, audio):
    """Regression: URLs and page used to contain the real arm/path in blind mode."""
    c, _ = make_client(tmp_path, audio, blind=True)
    index = c.get("/").get_data(as_text=True)
    links = track_links(c)
    assert len(links) == 3
    for l in links:
        assert "arm" not in l
        page = c.get(l).get_data(as_text=True)
        assert c.get(l).status_code == 200
        for secret in ("armA", "armB", "Prompt A", "secret prompt", "t1.wav", "t2.wav"):
            assert secret not in page, (l, secret)
    for secret in ("armA", "armB", "Prompt A"):
        assert secret not in index
    # audio is served by opaque id, and the real path is not addressable
    audio_src = re.search(r'src="(/audio/[^"]+)"', c.get(links[0]).get_data(as_text=True)).group(1)
    assert c.get(audio_src).status_code == 200
    assert c.get("/audio/armA/t1.wav").status_code == 404
    # the report still reveals the arms
    report = c.get("/report.md").get_data(as_text=True)
    assert "Prompt A" in report and "armB" in report


def test_blind_order_is_reproducible(tmp_path, audio):
    c1, _ = make_client(tmp_path, audio, blind=True)
    first = c1.get("/report.csv").get_data(as_text=True)
    c2, _ = make_client(tmp_path, audio, blind=True)  # same run folder => same stored seed
    assert c2.get("/report.csv").get_data(as_text=True) == first


def test_audio_traversal_blocked(tmp_path, audio):
    c, _ = make_client(tmp_path, audio)
    assert c.get("/audio/../../etc/passwd").status_code == 404
    assert c.get("/audio/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_default_scorecard_independent_of_cwd(tmp_path, monkeypatch):
    """Regression: from another cwd the default silently became a 2-question fallback."""
    monkeypatch.chdir(tmp_path)
    assert len(load_scorecard(None)["criteria"]) >= 6


def test_app_runs_from_other_directory(tmp_path, audio):
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from rating_app.settings import load_settings; from rating_app.web import create_app;"
        "s=load_settings(cli_args={'audio':%r,'out':%r,'runs_dir':%r,'label':'x'});"
        "c=create_app(s).test_client();"
        "html=c.get('/track/armB/t1.wav').get_data(as_text=True);"
        "print(html.count('question-wrapper\"'))"
    ) % (str(APP_ROOT), str(audio), str(tmp_path / "o"), str(tmp_path / "r"))
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip().splitlines()[-1]) >= 6


def test_bad_config_fails_loudly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        load_settings(cli_args={"config": str(bad)})
    with pytest.raises(FileNotFoundError):
        load_settings(cli_args={"config": str(tmp_path / "missing.json")})


def test_unknown_scorecard_fails_loudly(tmp_path, audio):
    s = load_settings(cli_args={"audio": str(audio), "out": str(tmp_path / "o"),
                                "runs_dir": str(tmp_path / "r"), "scorecard": "nope_nope"})
    with pytest.raises(FileNotFoundError):
        create_app(s)


def test_setup_with_bad_scorecard_is_rejected_and_reported(tmp_path, audio):
    c, s = make_client(tmp_path, audio)
    before = s.scorecard
    r = c.post("/setup", data={"audio": str(audio), "scorecard": "/nonexistent.json"})
    assert r.status_code == 302 and "err=" in r.headers["Location"]
    assert s.scorecard == before  # state not corrupted
    assert "Not applied" in c.get(r.headers["Location"]).get_data(as_text=True)


def test_criteria_load_missing_shows_error(tmp_path, audio):
    c, _ = make_client(tmp_path, audio)
    assert "not found" in c.get("/criteria?load=doesnotexist").get_data(as_text=True).lower()


def test_scorecard_save_refuses_overwrite_without_confirmation(tmp_path, audio):
    c, _ = make_client(tmp_path, audio)
    default = APP_ROOT / "scorecards" / "default.json"
    original = default.read_text()
    sc = json.loads(original)
    sc["criteria"] = sc["criteria"][:2]
    try:
        r = c.post("/criteria", data={"criteria_json": json.dumps(sc), "save_as_name": "default"})
        assert "err=" in r.headers["Location"]
        assert default.read_text() == original
    finally:
        default.write_text(original)


def test_legacy_out_criteria_json_is_honoured(tmp_path, audio):
    out = tmp_path / "out"
    out.mkdir()
    (out / "criteria.json").write_text(json.dumps([{"key": "legacy_q", "type": "rating", "max": 3}]))
    c, s = make_client(tmp_path, audio)
    assert "legacy_q" in c.get("/track/armB/t1.wav").get_data(as_text=True)
    assert "legacy" in s.sources["scorecard"]


def test_changing_folder_starts_a_separate_run(tmp_path, audio):
    other = tmp_path / "audio2"
    _wav(other / "x" / "a.wav")
    c, s = make_client(tmp_path, audio)
    c.post("/track/armB/t1.wav", data={"overall": "5", "fidelity": "5", "coherence": "5",
                                       "mix": "5", "artifacts": "none", "keep": "yes"})
    c.post("/setup", data={"audio": str(other), "label": "t"})
    runs = [p.name for p in (tmp_path / "runs").iterdir()]
    assert len(runs) == 2, runs
    assert "armB/t1.wav" not in c.get("/report.json").get_data(as_text=True)


def test_browse_restricted_off_loopback(tmp_path, audio):
    c, _ = make_client(tmp_path, audio, host="192.168.1.5")
    assert c.get("/api/browse?path=/etc").get_json()["current"] != "/etc"
    c2, _ = make_client(tmp_path, audio, host="127.0.0.1")
    assert c2.get("/api/browse?path=/etc").get_json()["current"] == "/etc"


def test_show_if_hidden_required_question_does_not_block_done(tmp_path):
    from rating_app.discovery import is_done
    crit = [{"key": "a", "type": "rating", "required": True},
            {"key": "why", "type": "choice", "required": True, "show_if": {"key": "a", "lte": 2}}]
    assert is_done({"a": "5"}, crit) is True
    assert is_done({"a": "1"}, crit) is False


def test_list_scale_labels_render(tmp_path, audio):
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps({"criteria": [{"key": "q", "type": "rating", "max": 3,
                                            "scale_labels": ["bad", "ok", "great"]}]}))
    c, _ = make_client(tmp_path, audio, scorecard=str(sc))
    assert "great" in c.get("/track/armB/t1.wav").get_data(as_text=True)
