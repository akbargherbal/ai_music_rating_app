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
from rating_app.paths import browse_directory
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


def create_app(settings: Settings) -> Flask:
    template_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(template_dir))

    out_dir = Path(settings.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve run_id
    run_id = getattr(settings, "run_id", None) or settings.label or "default_run"
    sanitized_run_id = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in run_id
    )

    # 2. Check for legacy migration from evaluations.json
    legacy_eval = out_dir / "evaluations.json"
    run_dir = Path(settings.runs_dir).expanduser().resolve() / sanitized_run_id
    target_results = run_dir / "results.json"
    if legacy_eval.is_file() and not target_results.is_file():
        migrate_legacy_evaluations(legacy_eval, target_results)

    # 3. Resolve active scorecard
    active_sc_dict = None
    sc_choice = settings.scorecard
    if sc_choice and Path(sc_choice).expanduser().is_file():
        active_sc_dict = load_scorecard(Path(sc_choice).expanduser())
    elif sc_choice:
        candidate = Path("scorecards") / f"{sc_choice}.json"
        if candidate.is_file():
            active_sc_dict = load_scorecard(candidate)
        else:
            legacy_audio_crit = (
                Path(settings.audio).expanduser() / "_criteria.json"
                if settings.audio
                else None
            )
            legacy_out_crit = out_dir / "criteria.json"
            if legacy_audio_crit and legacy_audio_crit.is_file():
                active_sc_dict = load_scorecard(legacy_audio_crit)
            elif legacy_out_crit.is_file():
                active_sc_dict = load_scorecard(legacy_out_crit)

    if not active_sc_dict:
        active_sc_dict = load_scorecard(None, fallback_default=True)

    # 4. Initialize Run
    run = Run(
        run_id=sanitized_run_id,
        runs_dir=settings.runs_dir,
        audio=settings.audio,
        label=settings.label,
        scorecard=active_sc_dict,
        settings_dict=settings.to_dict(),
    )

    # 5. In-memory cache for discovered tracks
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

    def get_context(**kwargs) -> dict[str, Any]:
        crit = active_sc_dict.get("criteria", [])
        total = len(cached_tracks)
        done_names = {
            t["name"]
            for t in cached_tracks
            if is_done(run.get_track_evaluation(t["name"]), crit)
        }
        done_count = len(done_names)
        pct = int(100 * done_count / (total or 1))
        return {
            "label": settings.label,
            "audio": settings.audio,
            "run_id": sanitized_run_id,
            "criteria": crit,
            "scorecard": active_sc_dict,
            "sections": sections_of(cached_tracks),
            "tracks": cached_tracks,
            "done": done_count,
            "total": total,
            "done_names": done_names,
            "pct": pct,
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

    @app.get("/track/<path:name>")
    def track(name: str):
        match = next((t for t in cached_tracks if t["name"] == name), None)
        if not match:
            abort(404)
        idx = cached_tracks.index(match)
        prev_name = cached_tracks[idx - 1]["name"] if idx > 0 else None
        next_name = (
            cached_tracks[idx + 1]["name"] if idx + 1 < len(cached_tracks) else None
        )

        rec = run.get_track_evaluation(name)
        return render_template(
            "track.html",
            t=match,
            rec=rec,
            pos=idx + 1,
            prev_name=prev_name,
            next_name=next_name,
            saved=request.args.get("saved") == "1",
            **get_context(),
        )

    @app.post("/track/<path:name>")
    def save(name: str):
        match = next((t for t in cached_tracks if t["name"] == name), None)
        if not match:
            abort(404)

        rec = run.get_track_evaluation(name)
        crit = active_sc_dict.get("criteria", [])

        for c in crit:
            ckey = c["key"]
            ctype = c["type"]
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

        if request.form.get("advance") == "1" and request.form.get("next"):
            return redirect(url_for("track", name=request.form["next"]))
        return redirect(url_for("track", name=name, saved=1))

    @app.get("/audio/<path:name>")
    def audio(name: str):
        if not settings.audio:
            abort(404)
        audio_root = Path(settings.audio).expanduser().resolve()
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            abort(404)
        target = (audio_root / p).resolve()
        if not target.is_file() or audio_root not in target.parents:
            abort(404)
        return send_from_directory(target.parent, target.name)

    @app.get("/setup")
    def setup():
        scorecard_lib = list_scorecards(settings.scorecard_dirs)
        return render_template(
            "setup.html",
            scorecard_library=scorecard_lib,
            current_scorecard=settings.scorecard,
            **get_context(),
        )

    @app.post("/setup")
    def setup_save():
        new_audio = request.form.get("audio", "").strip().strip('"').strip("'")
        new_label = request.form.get("label", "").strip()
        new_sc = request.form.get("scorecard", "").strip()

        if new_audio:
            settings.audio = new_audio
            settings.sources["audio"] = "web setup"
        if new_label:
            settings.label = new_label
            settings.sources["label"] = "web setup"
        if new_sc:
            settings.scorecard = new_sc
            settings.sources["scorecard"] = "web setup"
            nonlocal active_sc_dict
            try:
                candidate = Path("scorecards") / f"{new_sc}.json"
                if candidate.is_file():
                    active_sc_dict = load_scorecard(candidate)
                else:
                    active_sc_dict = load_scorecard(new_sc)
            except Exception:
                pass

        refresh_tracks()
        return redirect(url_for("index"))

    @app.get("/criteria")
    def criteria_page():
        scorecard_lib = list_scorecards(settings.scorecard_dirs)
        loaded_id = request.args.get("load", "")
        sc_data = active_sc_dict

        if loaded_id:
            candidate = Path("scorecards") / f"{loaded_id}.json"
            if candidate.is_file():
                try:
                    sc_data = load_scorecard(candidate)
                except Exception:
                    pass

        return render_template(
            "criteria.html",
            library=scorecard_lib,
            loaded_id=loaded_id,
            sc_name=sc_data.get("name", "Active Scorecard"),
            json_str=json.dumps(sc_data, indent=2, ensure_ascii=False),
            errors=request.args.getlist("err"),
            **get_context(),
        )

    @app.post("/criteria")
    def criteria_save():
        raw_json = request.form.get("criteria_json", "")
        save_as_name = request.form.get("save_as_name", "").strip() or "custom"
        save_as_name = "".join(
            c for c in save_as_name if c.isalnum() or c in ("-", "_")
        )

        try:
            parsed = json.loads(raw_json)
            errors = validate_scorecard(parsed)
            if errors:
                return redirect(url_for("criteria_page", err=errors))

            sc_dir = Path("scorecards")
            sc_dir.mkdir(parents=True, exist_ok=True)
            target_file = sc_dir / f"{save_as_name}.json"
            target_file.write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            nonlocal active_sc_dict
            active_sc_dict = load_scorecard(target_file)
            settings.scorecard = save_as_name
            settings.sources["scorecard"] = f"saved to {target_file}"
        except json.JSONDecodeError as e:
            return redirect(url_for("criteria_page", err=[f"Invalid JSON: {e}"]))

        return redirect(url_for("index"))

    @app.get("/settings")
    def settings_page():
        drift = run.detect_drift(active_sc_dict)
        return render_template(
            "settings.html",
            settings_dict=settings.to_dict(),
            sources=settings.sources,
            drift=drift,
            **get_context(),
        )

    @app.get("/report")
    def report():
        md = render_markdown(
            cached_tracks,
            {"tracks": run.tracks_data, "meta": run.meta},
            active_sc_dict,
            settings.audio or Path("."),
            settings.label,
            settings=settings,
        )
        return render_template("report.html", md=md, **get_context())

    @app.get("/report.md")
    def report_md():
        md = render_markdown(
            cached_tracks,
            {"tracks": run.tracks_data, "meta": run.meta},
            active_sc_dict,
            settings.audio or Path("."),
            settings.label,
            settings=settings,
        )
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            md,
            mimetype="text/markdown",
            headers={"Content-Disposition": f"attachment; filename=rating_{stamp}.md"},
        )

    @app.get("/report.csv")
    def report_csv():
        csv_data = render_csv(
            cached_tracks,
            {"tracks": run.tracks_data, "meta": run.meta},
            active_sc_dict,
            settings=settings,
        )
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=rating_{stamp}.csv"},
        )

    @app.get("/report.json")
    def report_json():
        json_data = render_json(
            cached_tracks,
            {"tracks": run.tracks_data, "meta": run.meta},
            active_sc_dict,
            settings=settings,
        )
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M")
        return Response(
            json.dumps(json_data, indent=2, ensure_ascii=False),
            mimetype="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=rating_{stamp}.json"
            },
        )

    @app.get("/api/browse")
    def api_browse():
        req_path = request.args.get("path")
        allowed_roots = [Path.cwd()] if settings.host == "0.0.0.0" else None
        res = browse_directory(req_path, allowed_roots=allowed_roots)
        return jsonify(res)

    return app
