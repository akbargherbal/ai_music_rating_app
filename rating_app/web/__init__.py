"""rating_app.web — Flask application factory and route endpoints."""

from __future__ import annotations

import json
import socket
import datetime as _dt
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from rating_app.discovery import discover, is_done, sections_of
from rating_app.paths import (
    APP_ROOT,
    SCORECARD_DIR,
    app_path,
    browse_directory,
    is_loopback_host,
)
from rating_app.report import render_csv, render_json, render_markdown
from rating_app.scorecard import (
    list_scorecards,
    load_scorecard,
    validate_scorecard,
)
from rating_app.settings import Settings
from rating_app.store import Run, migrate_legacy_evaluations


def pick_free_port(host: str, preferred: int, scan: int = 50) -> int:
    """Scan for a free port starting at preferred; fallback to ephemeral port."""
    bind_host = host or "127.0.0.1"
    candidates = (0,) if preferred <= 0 else range(preferred, preferred + scan)
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((bind_host, port))
                return s.getsockname()[1]
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((bind_host, 0))
        return s.getsockname()[1]


def _sanitize(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name) or "default_run"


def create_app(settings: Settings) -> Flask:
    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))

    out_dir = Path(settings.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = app_path(settings.runs_dir)

    # ---- scorecard resolution (explicit; errors are raised, never swallowed) ----
    def resolve_scorecard(choice: str) -> dict:
        direct = Path(choice).expanduser()
        if direct.is_file():
            return load_scorecard(direct)
        for d in [*settings.scorecard_dirs, SCORECARD_DIR]:
            cand = app_path(d) / f"{choice}.json"
            if cand.is_file():
                return load_scorecard(cand)
        raise FileNotFoundError(f"Scorecard not found: {choice}")

    def initial_scorecard() -> dict:
        # Legacy shim: only when the user did not pick a scorecard explicitly.
        if settings.sources.get("scorecard", "default") == "default":
            legacy = [out_dir / "criteria.json"]
            if settings.audio:
                legacy.append(Path(settings.audio).expanduser() / "_criteria.json")
            for f in legacy:
                if f.is_file():
                    settings.sources["scorecard"] = f"legacy file: {f}"
                    return load_scorecard(f)
        return resolve_scorecard(settings.scorecard)

    active_sc_dict = initial_scorecard()

    # ---- runs ----
    def pick_run_id(label: str, audio: str) -> str:
        """One run per (label, audio folder): same label + different folder = new run."""
        base = _sanitize(getattr(settings, "run_id", None) or label or "default_run")
        meta = Run.peek_meta(runs_dir, base)
        if meta and meta.get("audio") and audio and meta["audio"] != audio:
            import hashlib
            return f"{base}-{hashlib.sha1(audio.encode()).hexdigest()[:6]}"
        return base

    def make_run() -> Run:
        rid = pick_run_id(settings.label, settings.audio)
        legacy_eval = out_dir / "evaluations.json"
        target = runs_dir / rid / "results.json"
        if legacy_eval.is_file() and not target.is_file():
            migrate_legacy_evaluations(legacy_eval, target)
        return Run(
            run_id=rid,
            runs_dir=runs_dir,
            audio=settings.audio,
            label=settings.label,
            scorecard=active_sc_dict,
            settings_dict=settings.to_dict(),
        )

    run = make_run()

    cached_tracks: list[dict[str, Any]] = []

    def refresh_tracks():
        nonlocal cached_tracks
        if settings.audio and Path(settings.audio).expanduser().is_dir():
            cached_tracks = discover(
                audio_dir=settings.audio,
                audio_exts=settings.audio_exts,
                group_by=settings.group_by,
                blind=settings.blind,
                blind_seed=run.meta.get("blind_seed"),
            )
        else:
            cached_tracks = []

    refresh_tracks()

    def find_track(tid: str):
        return next((t for t in cached_tracks if t["id"] == tid), None)

    def results_payload() -> dict:
        return {"tracks": run.tracks_data, "meta": run.meta}

    def get_context(**kwargs) -> dict[str, Any]:
        crit = active_sc_dict.get("criteria", [])
        total = len(cached_tracks)
        done_names = {
            t["name"]
            for t in cached_tracks
            if is_done(run.get_track_evaluation(t["name"]), crit)
        }
        done_count = len(done_names)
        return {
            "label": settings.label,
            "audio": settings.audio,
            "run_id": run.run_id,
            "criteria": crit,
            "scorecard": active_sc_dict,
            "sections": sections_of(cached_tracks),
            "tracks": cached_tracks,
            "blind": settings.blind,
            "done": done_count,
            "total": total,
            "done_names": done_names,
            "pct": int(100 * done_count / (total or 1)),
            **kwargs,
        }

    @app.get("/")
    def index():
        return render_template(
            "index.html", saved=request.args.get("saved") == "1", **get_context()
        )

    @app.get("/rescan")
    def rescan():
        refresh_tracks()
        return redirect(url_for("index"))

    @app.get("/track/<path:tid>")
    def track(tid: str):
        match = find_track(tid)
        if not match:
            abort(404)
        idx = cached_tracks.index(match)
        prev_id = cached_tracks[idx - 1]["id"] if idx > 0 else None
        next_id = cached_tracks[idx + 1]["id"] if idx + 1 < len(cached_tracks) else None
        return render_template(
            "track.html",
            t=match,
            rec=run.get_track_evaluation(match["name"]),
            pos=idx + 1,
            prev_id=prev_id,
            next_id=next_id,
            saved=request.args.get("saved") == "1",
            **get_context(),
        )

    @app.post("/track/<path:tid>")
    def save(tid: str):
        match = find_track(tid)
        if not match:
            abort(404)
        name = match["name"]
        rec = run.get_track_evaluation(name)
        for c in active_sc_dict.get("criteria", []):
            ckey, ctype = c["key"], c["type"]
            if ctype == "multi_choice":
                vals = request.form.getlist(ckey)
                if vals:
                    rec[ckey] = vals
                else:
                    rec.pop(ckey, None)
            else:
                val = request.form.get(ckey, "").strip()
                if val == "":
                    rec.pop(ckey, None)
                elif ctype == "number":
                    try:
                        rec[ckey] = float(val) if "." in val else int(val)
                    except ValueError:
                        rec[ckey] = val
                elif ctype == "boolean":
                    rec[ckey] = val.lower() == "true"
                else:
                    rec[ckey] = val
        run.set_track_evaluation(name, rec)

        nxt = request.form.get("next")
        if request.form.get("advance") == "1" and nxt and find_track(nxt):
            return redirect(url_for("track", tid=nxt))
        return redirect(url_for("track", tid=tid, saved=1))

    @app.get("/audio/<path:tid>")
    def audio(tid: str):
        match = find_track(tid)  # lookup by opaque id; never a user-supplied path
        if not match or not settings.audio:
            abort(404)
        audio_root = Path(settings.audio).expanduser().resolve()
        target = (audio_root / match["file"]).resolve()
        if not target.is_file() or audio_root not in target.parents:
            abort(404)
        return send_from_directory(target.parent, target.name)

    @app.get("/setup")
    def setup():
        return render_template(
            "setup.html",
            scorecard_library=list_scorecards(settings.scorecard_dirs),
            current_scorecard=settings.scorecard,
            errors=request.args.getlist("err"),
            **get_context(),
        )

    @app.post("/setup")
    def setup_save():
        nonlocal active_sc_dict, run
        new_audio = request.form.get("audio", "").strip().strip('"').strip("'")
        new_label = request.form.get("label", "").strip()
        new_sc = request.form.get("scorecard", "").strip()

        errors: list[str] = []
        new_sc_dict = None
        if new_sc:
            try:
                new_sc_dict = resolve_scorecard(new_sc)
            except (FileNotFoundError, ValueError) as e:
                errors.append(str(e))
        if new_audio and not Path(new_audio).expanduser().is_dir():
            errors.append(f"Audio folder not found: {new_audio}")
        if errors:  # nothing is applied unless everything is valid
            return redirect(url_for("setup", err=errors))

        if new_sc_dict is not None:
            active_sc_dict = new_sc_dict
            settings.scorecard = new_sc
            settings.sources["scorecard"] = "web setup"
        if new_audio:
            settings.audio = new_audio
            settings.sources["audio"] = "web setup"
        if new_label:
            settings.label = new_label
            settings.sources["label"] = "web setup"

        run = make_run()  # new folder/label => its own run folder, results never mix
        refresh_tracks()
        return redirect(url_for("index"))

    @app.get("/criteria")
    def criteria_page():
        loaded_id = request.args.get("load", "")
        errors = request.args.getlist("err")
        sc_data = active_sc_dict
        if loaded_id:
            try:
                sc_data = resolve_scorecard(loaded_id)
            except (FileNotFoundError, ValueError) as e:
                errors.append(str(e))
        return render_template(
            "criteria.html",
            library=list_scorecards(settings.scorecard_dirs),
            loaded_id=loaded_id,
            sc_name=sc_data.get("name", "Active Scorecard"),
            json_str=json.dumps(sc_data, indent=2, ensure_ascii=False),
            errors=errors,
            **get_context(),
        )

    @app.post("/criteria")
    def criteria_save():
        nonlocal active_sc_dict
        raw_json = request.form.get("criteria_json", "")
        name = "".join(
            c for c in (request.form.get("save_as_name", "").strip() or "custom")
            if c.isalnum() or c in ("-", "_")
        ) or "custom"
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as e:
            return redirect(url_for("criteria_page", err=[f"Invalid JSON: {e}"]))
        errors = validate_scorecard(parsed)
        if errors:
            return redirect(url_for("criteria_page", err=errors))

        SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
        target = SCORECARD_DIR / f"{name}.json"
        if target.exists() and request.form.get("overwrite") != "1":
            return redirect(url_for(
                "criteria_page",
                err=[f"'{name}.json' already exists. Tick 'overwrite existing' or choose another name."],
            ))
        target.write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        active_sc_dict = load_scorecard(target)
        settings.scorecard = name
        settings.sources["scorecard"] = f"saved to {target}"
        return redirect(url_for("index"))

    @app.get("/settings")
    def settings_page():
        return render_template(
            "settings.html",
            settings_dict=settings.to_dict(),
            sources=settings.sources,
            drift=run.detect_drift(active_sc_dict),
            **get_context(),
        )

    def _stamp() -> str:
        return _dt.datetime.now().strftime("%Y%m%d-%H%M")

    def _md() -> str:
        return render_markdown(
            cached_tracks, results_payload(), active_sc_dict,
            settings.audio or Path("."), settings.label, settings=settings,
        )

    @app.get("/report")
    def report():
        return render_template("report.html", md=_md(), **get_context())

    @app.get("/report.md")
    def report_md():
        return Response(_md(), mimetype="text/markdown", headers={
            "Content-Disposition": f"attachment; filename=rating_{_stamp()}.md"})

    @app.get("/report.csv")
    def report_csv():
        data = render_csv(cached_tracks, results_payload(), active_sc_dict, settings=settings)
        return Response(data, mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename=rating_{_stamp()}.csv"})

    @app.get("/report.json")
    def report_json():
        data = render_json(cached_tracks, results_payload(), active_sc_dict, settings=settings)
        return Response(json.dumps(data, indent=2, ensure_ascii=False),
                        mimetype="application/json", headers={
            "Content-Disposition": f"attachment; filename=rating_{_stamp()}.json"})

    @app.get("/api/browse")
    def api_browse():
        # Off-loopback: confine browsing to the working directory tree.
        roots = None if is_loopback_host(settings.host) else [Path.cwd()]
        return jsonify(browse_directory(request.args.get("path"), allowed_roots=roots))

    return app
