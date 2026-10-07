"""Route-level tests (Flask test client).

These pin the regressions found in review - track-page crash with metadata, blind-mode
leaks, cwd-dependence, silent failures - and exercise every endpoint and branch of
``rating_app.web``.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import DEFAULT_ANSWERS, REAL_SCORECARDS, REPO_ROOT
from rating_app.settings import load_settings
from rating_app.web import _sanitize, create_app, pick_free_port

PAGES = ["/", "/setup", "/criteria", "/settings", "/report", "/report.md", "/report.csv", "/report.json"]


def read_results(tmp_path):
    return json.loads(next((tmp_path / "runs").glob("*/results.json")).read_text())


def errors_in(location: str) -> list[str]:
    return parse_qs(urlparse(location).query).get("err", [])


# ====================================================================== smoke / rendering
def test_all_pages_ok(make_client):
    c, _ = make_client()
    for url in PAGES:
        assert c.get(url).status_code == 200, url


def test_all_pages_ok_without_any_audio_folder(tmp_path):
    s = load_settings(cli_args={"out": str(tmp_path / "o"), "runs_dir": str(tmp_path / "r"), "label": ""})
    c = create_app(s).test_client()
    for url in PAGES:
        assert c.get(url).status_code == 200, url
    assert c.get("/track/anything").status_code == 404
    assert c.get("/audio/anything").status_code == 404
    assert list((tmp_path / "r").iterdir())[0].name == "default_run"


def test_track_page_with_metadata_renders(make_client, track_links):
    """Regression: `% project %` typo made every track with metadata return 500."""
    c, _ = make_client()
    links = track_links(c)
    assert len(links) == 3
    for link in links:
        assert c.get(link).status_code == 200, link
    page = c.get("/track/armA/t1.wav").get_data(as_text=True)
    assert "secret prompt" in page and "seed" in page


def test_every_question_type_renders_and_saves(make_client, full_scorecard, tmp_path):
    c, _ = make_client(scorecard=str(full_scorecard))
    page = c.get("/track/armB/t1.wav").get_data(as_text=True)
    for needle in ("overall", "pick", "tags", "bpm", "notes", "Ex", "great", "Yes", "No"):
        assert needle in page, needle
    r = c.post("/track/armB/t1.wav", data={
        "overall": "5", "pick": "b", "tags": ["x", "y"], "ok": "true", "bpm": "120", "notes": "  hi  ",
    })
    assert r.status_code == 302
    rec = read_results(tmp_path)["tracks"]["armB/t1.wav"]
    assert rec["overall"] == "5" and rec["pick"] == "b" and rec["tags"] == ["x", "y"]
    assert rec["ok"] is True and rec["bpm"] == 120 and rec["notes"] == "hi"


def test_list_scale_labels_render(make_client, tmp_path):
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps({"criteria": [{"key": "q", "type": "rating", "max": 3,
                                            "scale_labels": ["bad", "ok", "great"]}]}))
    c, _ = make_client(scorecard=str(sc))
    assert "great" in c.get("/track/armB/t1.wav").get_data(as_text=True)


def test_track_navigation_links(make_client, track_links):
    c, _ = make_client()
    first, middle, last = track_links(c)
    page_first = c.get(first).get_data(as_text=True)
    assert "← previous" not in page_first and "next →" in page_first
    page_mid = c.get(middle).get_data(as_text=True)
    assert "← previous" in page_mid and "next →" in page_mid
    assert f'href="{first}"' in page_mid and f'href="{last}"' in page_mid
    page_last = c.get(last).get_data(as_text=True)
    assert "← previous" in page_last and "next →" not in page_last
    assert "Save &amp; next" not in page_last  # no advance button on the final track


def test_saved_flash_messages(make_client):
    c, _ = make_client()
    assert "Evaluation saved" in c.get("/?saved=1").get_data(as_text=True)
    assert "Evaluation saved" not in c.get("/").get_data(as_text=True)
    assert "✓ Saved." in c.get("/track/armB/t1.wav?saved=1").get_data(as_text=True)


# ====================================================================== saving results
def test_save_and_report_roundtrip(make_client, tmp_path):
    c, _ = make_client()
    r = c.post("/track/armB/t1.wav", data=DEFAULT_ANSWERS)
    assert r.status_code == 302 and r.headers["Location"].endswith("saved=1")
    assert read_results(tmp_path)["tracks"]["armB/t1.wav"]["overall"] == "4"
    assert "armB" in c.get("/report.md").get_data(as_text=True)


def test_save_unknown_track_is_404(make_client):
    c, _ = make_client()
    assert c.post("/track/ghost.wav", data={"overall": "1"}).status_code == 404
    assert c.get("/track/ghost.wav").status_code == 404


def test_blank_answers_remove_previous_values(make_client, full_scorecard, tmp_path):
    c, _ = make_client(scorecard=str(full_scorecard))
    c.post("/track/armB/t1.wav", data={"overall": "5", "tags": ["x"], "notes": "n", "bpm": "7"})
    c.post("/track/armB/t1.wav", data={"overall": "", "notes": "   "})  # tags absent -> cleared
    rec = read_results(tmp_path)["tracks"]["armB/t1.wav"]
    assert set(rec) == {"updated"}


@pytest.mark.parametrize("raw, expected", [("42", 42), ("4.5", 4.5), ("abc", "abc")])
def test_number_coercion(make_client, full_scorecard, tmp_path, raw, expected):
    c, _ = make_client(scorecard=str(full_scorecard))
    c.post("/track/armB/t1.wav", data={"bpm": raw})
    assert read_results(tmp_path)["tracks"]["armB/t1.wav"]["bpm"] == expected


@pytest.mark.parametrize("raw, expected", [("true", True), ("TRUE", True), ("false", False), ("other", False)])
def test_boolean_coercion(make_client, full_scorecard, tmp_path, raw, expected):
    c, _ = make_client(scorecard=str(full_scorecard))
    c.post("/track/armB/t1.wav", data={"ok": raw})
    assert read_results(tmp_path)["tracks"]["armB/t1.wav"]["ok"] is expected


def test_save_and_advance_goes_to_next_track(make_client, track_links):
    c, _ = make_client()
    first, second, _ = track_links(c)
    nxt = second.removeprefix("/track/")
    r = c.post(first, data={**DEFAULT_ANSWERS, "advance": "1", "next": nxt})
    assert r.headers["Location"].endswith(second)
    assert "saved=1" not in r.headers["Location"]


@pytest.mark.parametrize("extra", [{"advance": "1", "next": "ghost.wav"}, {"advance": "1"}, {"next": "x"}])
def test_advance_without_valid_next_stays_on_track(make_client, track_links, extra):
    c, _ = make_client()
    first = track_links(c)[0]
    r = c.post(first, data={**DEFAULT_ANSWERS, **extra})
    assert r.headers["Location"].endswith(first + "?saved=1")


# ====================================================================== blind mode
def test_blind_mode_does_not_leak_arm(make_client, track_links):
    """Regression: URLs and page used to contain the real arm/path in blind mode."""
    c, _ = make_client(blind=True)
    index = c.get("/").get_data(as_text=True)
    links = track_links(c)
    assert len(links) == 3
    secrets = ("armA", "armB", "Prompt A", "secret prompt", "t1.wav", "t2.wav")
    for link in links:
        assert "arm" not in link
        resp = c.get(link)
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        for secret in secrets:
            assert secret not in page, (link, secret)
    for secret in ("armA", "armB", "Prompt A"):
        assert secret not in index
    src = re.search(r'src="(/audio/[^"]+)"', c.get(links[0]).get_data(as_text=True)).group(1)
    with c.get(src) as ok:  # audio is served by opaque id...
        assert ok.status_code == 200
    assert c.get("/audio/armA/t1.wav").status_code == 404  # ...and the real path is not addressable
    report = c.get("/report.md").get_data(as_text=True)  # the report still reveals the arms
    assert "Prompt A" in report and "armB" in report


def test_blind_order_is_reproducible(make_client):
    c1, _ = make_client(blind=True)
    first = c1.get("/report.csv").get_data(as_text=True)
    c2, _ = make_client(blind=True)  # same run folder => same stored seed
    assert c2.get("/report.csv").get_data(as_text=True) == first


# ====================================================================== audio endpoint
def test_audio_serves_wav_bytes(make_client):
    c, _ = make_client()
    with c.get("/audio/armB/t1.wav") as r:
        assert r.status_code == 200
        assert r.data[:4] == b"RIFF"


def test_audio_traversal_blocked(make_client):
    c, _ = make_client()
    assert c.get("/audio/../../etc/passwd").status_code == 404
    assert c.get("/audio/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_audio_unknown_track_is_404(make_client):
    c, _ = make_client()
    assert c.get("/audio/armB/ghost.wav").status_code == 404


def test_audio_file_deleted_after_scan_is_404(make_client, audio):
    c, _ = make_client()
    (audio / "armB" / "t1.wav").unlink()
    assert c.get("/audio/armB/t1.wav").status_code == 404


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_audio_symlink_escaping_the_audio_root_is_404(make_client, audio, tmp_path, wav):
    outside = wav(tmp_path / "outside" / "secret.wav")
    link = audio / "armC" / "link.wav"
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted")
    c, _ = make_client()
    assert c.get("/audio/armC/link.wav").status_code == 404


# ====================================================================== rescan
def test_rescan_picks_up_new_files(make_client, audio, wav, track_links):
    c, _ = make_client()
    assert len(track_links(c)) == 3
    wav(audio / "armB" / "t9.wav")
    assert len(track_links(c)) == 3  # cached until rescan
    r = c.get("/rescan")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert len(track_links(c)) == 4


# ====================================================================== setup
def test_setup_page_lists_scorecard_library(make_client):
    c, _ = make_client()
    page = c.get("/setup").get_data(as_text=True)
    assert "arabic_vocal" in page and "default" in page


@pytest.mark.parametrize("url", ["/setup", "/criteria"])
def test_scorecard_library_is_independent_of_cwd(make_client, tmp_path, monkeypatch, url):
    c, _ = make_client()
    monkeypatch.chdir(tmp_path)
    assert "arabic_vocal" in c.get(url).get_data(as_text=True)


def test_setup_with_bad_scorecard_is_rejected_and_reported(make_client, audio):
    c, s = make_client()
    before = s.scorecard
    r = c.post("/setup", data={"audio": str(audio), "scorecard": "/nonexistent.json"})
    assert r.status_code == 302 and "err=" in r.headers["Location"]
    assert s.scorecard == before  # state not corrupted
    assert "Not applied" in c.get(r.headers["Location"]).get_data(as_text=True)


def test_setup_with_missing_audio_folder_is_rejected(make_client, tmp_path):
    c, s = make_client()
    before = s.audio
    r = c.post("/setup", data={"audio": str(tmp_path / "nowhere"), "label": "changed"})
    assert any("Audio folder not found" in e for e in errors_in(r.headers["Location"]))
    assert s.audio == before and s.label == "t"  # nothing applied, not even the valid label


def test_setup_collects_every_error_at_once(make_client, tmp_path):
    c, _ = make_client()
    r = c.post("/setup", data={"audio": str(tmp_path / "nowhere"), "scorecard": "nope_nope"})
    assert len(errors_in(r.headers["Location"])) == 2


def test_setup_applies_scorecard_audio_and_label(make_client, tmp_path, wav):
    other = tmp_path / "other"
    wav(other / "x" / "a.wav")
    c, s = make_client()
    r = c.post("/setup", data={"audio": f' "{other}" ', "label": "New label", "scorecard": "arabic_vocal"})
    assert r.headers["Location"] == "/"
    assert (s.audio, s.label, s.scorecard) == (str(other), "New label", "arabic_vocal")
    assert all(s.sources[k] == "web setup" for k in ("audio", "label", "scorecard"))
    page = c.get("/track/x/a.wav").get_data(as_text=True)
    assert "melody" in page  # new scorecard is live


def test_setup_with_empty_form_changes_nothing(make_client):
    c, s = make_client()
    snapshot = s.to_dict()
    assert c.post("/setup", data={}).headers["Location"] == "/"
    assert s.to_dict() == snapshot


def test_changing_folder_starts_a_separate_run(make_client, tmp_path, wav):
    other = tmp_path / "audio2"
    wav(other / "x" / "a.wav")
    c, _ = make_client()
    c.post("/track/armB/t1.wav", data=DEFAULT_ANSWERS)
    c.post("/setup", data={"audio": str(other), "label": "t"})
    runs = [p.name for p in (tmp_path / "runs").iterdir()]
    assert len(runs) == 2, runs
    assert "armB/t1.wav" not in c.get("/report.json").get_data(as_text=True)


# ====================================================================== criteria editor
def test_criteria_page_shows_active_scorecard_json(make_client):
    c, _ = make_client()
    page = c.get("/criteria").get_data(as_text=True)
    assert "overall" in page and "summary_metric" in page


def test_criteria_load_existing_scorecard(make_client):
    c, _ = make_client()
    page = c.get("/criteria?load=arabic_vocal").get_data(as_text=True)
    assert "prosody" in page


def test_criteria_load_missing_shows_error(make_client):
    c, _ = make_client()
    assert "not found" in c.get("/criteria?load=doesnotexist").get_data(as_text=True).lower()


def test_criteria_load_invalid_scorecard_shows_error(make_client, app_root):
    (app_root / "scorecards" / "broken.json").write_text("{nope")
    c, _ = make_client()
    assert "invalid json" in c.get("/criteria?load=broken").get_data(as_text=True).lower()


def test_criteria_save_rejects_invalid_json(make_client):
    c, _ = make_client()
    r = c.post("/criteria", data={"criteria_json": "{nope"})
    assert any("Invalid JSON" in e for e in errors_in(r.headers["Location"]))


def test_criteria_save_rejects_failed_validation(make_client):
    c, _ = make_client()
    r = c.post("/criteria", data={"criteria_json": json.dumps({"criteria": []})})
    assert any("non-empty" in e for e in errors_in(r.headers["Location"]))


def test_criteria_save_creates_file_and_activates_it(make_client, app_root, tmp_path):
    c, s = make_client()
    sc = {"name": "Mine", "criteria": [{"key": "only_q", "type": "rating"}]}
    r = c.post("/criteria", data={"criteria_json": json.dumps(sc), "save_as_name": "My card!!"})
    assert r.headers["Location"] == "/"
    saved = app_root / "scorecards" / "Mycard.json"  # name sanitised to [A-Za-z0-9_-]
    assert json.loads(saved.read_text())["name"] == "Mine"
    assert s.scorecard == "Mycard" and s.sources["scorecard"].startswith("saved to")
    assert "only_q" in c.get("/track/armB/t1.wav").get_data(as_text=True)
    assert not (REAL_SCORECARDS / "Mycard.json").exists()  # sandbox kept the repo clean


def test_criteria_save_without_name_defaults_to_custom(make_client, app_root):
    c, _ = make_client()
    c.post("/criteria", data={"criteria_json": json.dumps([{"key": "q"}]), "save_as_name": "!!!"})
    assert (app_root / "scorecards" / "custom.json").is_file()


def test_scorecard_save_refuses_overwrite_without_confirmation(make_client, app_root):
    c, _ = make_client()
    default = app_root / "scorecards" / "default.json"
    original = default.read_text()
    sc = json.loads(original)
    sc["criteria"] = sc["criteria"][:2]
    r = c.post("/criteria", data={"criteria_json": json.dumps(sc), "save_as_name": "default"})
    assert any("already exists" in e for e in errors_in(r.headers["Location"]))
    assert default.read_text() == original


def test_scorecard_save_overwrites_when_confirmed(make_client, app_root):
    c, _ = make_client()
    sc = {"name": "Replaced", "criteria": [{"key": "q"}]}
    r = c.post("/criteria", data={"criteria_json": json.dumps(sc), "save_as_name": "default", "overwrite": "1"})
    assert r.headers["Location"] == "/"
    assert json.loads((app_root / "scorecards" / "default.json").read_text())["name"] == "Replaced"


# ====================================================================== scorecard resolution at startup
def test_unknown_scorecard_fails_loudly(tmp_path, audio):
    s = load_settings(cli_args={"audio": str(audio), "out": str(tmp_path / "o"),
                                "runs_dir": str(tmp_path / "r"), "scorecard": "nope_nope"})
    with pytest.raises(FileNotFoundError, match="Scorecard not found"):
        create_app(s)


def test_scorecard_by_direct_path(make_client, full_scorecard):
    c, _ = make_client(scorecard=str(full_scorecard))
    assert "bpm" in c.get("/track/armB/t1.wav").get_data(as_text=True)


def test_legacy_out_criteria_json_is_honoured(make_client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "criteria.json").write_text(json.dumps([{"key": "legacy_q", "type": "rating", "max": 3}]))
    c, s = make_client()
    assert "legacy_q" in c.get("/track/armB/t1.wav").get_data(as_text=True)
    assert "legacy" in s.sources["scorecard"]


def test_legacy_audio_folder_criteria_is_honoured(make_client, audio):
    (audio / "_criteria.json").write_text(json.dumps([{"key": "folder_q", "type": "rating"}]))
    c, _ = make_client()
    assert "folder_q" in c.get("/track/armB/t1.wav").get_data(as_text=True)


def test_explicit_scorecard_beats_legacy_files(make_client, tmp_path, full_scorecard):
    out = tmp_path / "out"
    out.mkdir()
    (out / "criteria.json").write_text(json.dumps([{"key": "legacy_q"}]))
    c, _ = make_client(scorecard=str(full_scorecard))
    page = c.get("/track/armB/t1.wav").get_data(as_text=True)
    assert "legacy_q" not in page and "bpm" in page


# ====================================================================== runs
def test_legacy_evaluations_json_is_migrated_into_new_run(make_client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "evaluations.json").write_text(json.dumps({"tracks": {"armB/t1.wav": {"overall": 5}}}))
    c, _ = make_client()
    assert json.loads(c.get("/report.json").get_data(as_text=True))["tracks"]["armB/t1.wav"]["overall"] == 5
    assert (tmp_path / "runs" / "t" / "results.json").is_file()


def test_results_persist_across_app_restarts(make_client):
    c1, _ = make_client()
    c1.post("/track/armB/t1.wav", data=DEFAULT_ANSWERS)
    c2, _ = make_client()
    assert json.loads(c2.get("/report.json").get_data(as_text=True))["tracks"]["armB/t1.wav"]["overall"] == "4"


def test_custom_run_id_attribute_is_sanitised(tmp_path, audio):
    s = load_settings(cli_args={"audio": str(audio), "out": str(tmp_path / "o"),
                                "runs_dir": str(tmp_path / "r"), "label": "ignored"})
    s.run_id = "my run!"  # explicit run id; sanitised to my_run_
    create_app(s)
    assert [p.name for p in (tmp_path / "r").iterdir()] == ["my_run_"]


def test_sanitize():
    assert _sanitize("ok-name_1") == "ok-name_1"
    assert _sanitize("a b/c") == "a_b_c"
    assert _sanitize("") == "default_run"


# ====================================================================== settings page & drift
def test_settings_page_lists_effective_values_and_sources(make_client):
    c, _ = make_client()
    page = c.get("/settings").get_data(as_text=True)
    assert "CLI argument" in page and "127.0.0.1" in page
    assert "Drift Detected" not in page


def test_settings_page_reports_scorecard_drift(make_client):
    c, _ = make_client()
    sc = {"name": "Changed", "criteria": [{"key": "brand_new_q", "type": "rating"}]}
    c.post("/criteria", data={"criteria_json": json.dumps(sc), "save_as_name": "changed"})
    page = c.get("/settings").get_data(as_text=True)
    assert "Drift Detected" in page and "brand_new_q" in page and "overall" in page


# ====================================================================== reports
def test_report_endpoints_headers_and_content(make_client):
    c, _ = make_client()
    c.post("/track/armB/t1.wav", data=DEFAULT_ANSWERS)
    for url, mimetype, ext in [("/report.md", "text/markdown", "md"), ("/report.csv", "text/csv", "csv"),
                               ("/report.json", "application/json", "json")]:
        r = c.get(url)
        assert r.mimetype == mimetype
        assert re.fullmatch(rf'attachment; filename=rating_\d{{8}}-\d{{4}}\.{ext}',
                            r.headers["Content-Disposition"])
    assert json.loads(c.get("/report.json").get_data(as_text=True))["summary"]["total_tracks"] == 3
    assert c.get("/report.csv").get_data(as_text=True).splitlines()[0].startswith("file,folder_arm")
    assert "Listening rating report" in c.get("/report").get_data(as_text=True)


# ====================================================================== folder browser
def test_browse_is_confined_to_cwd_off_loopback(make_client, audio, monkeypatch):
    home, elsewhere = (audio / "armA").resolve(), (audio / "armB").resolve()
    monkeypatch.chdir(home)  # off-loopback the only allowed root is the working directory
    query = {"query_string": {"path": str(elsewhere)}}
    c_remote, _ = make_client(host="192.168.1.5")
    assert Path(c_remote.get("/api/browse", **query).get_json()["current"]) == home
    c_local, _ = make_client(host="127.0.0.1")
    assert Path(c_local.get("/api/browse", **query).get_json()["current"]) == elsewhere


def test_browse_lists_subfolders(make_client, audio):
    c, _ = make_client()
    data = c.get("/api/browse", query_string={"path": str(audio)}).get_json()
    assert {d["name"] for d in data["dirs"]} == {"armA", "armB"}


# ====================================================================== pick_free_port
def _listening_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_pick_free_port_prefers_requested_port_when_free():
    s, port = _listening_socket()
    s.close()
    assert pick_free_port("127.0.0.1", port) == port


def test_pick_free_port_skips_busy_port():
    s, port = _listening_socket()
    try:
        got = pick_free_port("127.0.0.1", port)
        assert got != port and port < got < port + 50
    finally:
        s.close()


def test_pick_free_port_falls_back_to_ephemeral_when_scan_exhausted():
    s, port = _listening_socket()
    try:
        got = pick_free_port("127.0.0.1", port, scan=1)
        assert got != port and got > 0
    finally:
        s.close()


@pytest.mark.parametrize("preferred", [0, -1])
def test_pick_free_port_non_positive_means_ephemeral(preferred):
    assert pick_free_port("", preferred) > 0  # empty host => loopback


# ====================================================================== cwd independence
def test_app_runs_from_other_directory(tmp_path, audio):
    code = (
        "import sys; sys.path.insert(0, %r);"
        "from rating_app.settings import load_settings; from rating_app.web import create_app;"
        "s=load_settings(cli_args={'audio':%r,'out':%r,'runs_dir':%r,'label':'x'});"
        "c=create_app(s).test_client();"
        "html=c.get('/track/armB/t1.wav').get_data(as_text=True);"
        "print(html.count('question-wrapper\"'))"
    ) % (str(REPO_ROOT), str(audio), str(tmp_path / "o"), str(tmp_path / "r"))
    # Don't let pytest-cov's .pth hook measure the child: its data can't be combined with ours.
    env = {k: v for k, v in os.environ.items() if not k.startswith("COV_CORE")}
    env["HOME"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert int(out.stdout.strip().splitlines()[-1]) >= 6
