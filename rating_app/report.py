"""rating_app.report — multi-metric Markdown, CSV, and JSON report generation."""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import math
from pathlib import Path
from typing import Any, Sequence


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _calc_stats(vals: Sequence[float]) -> tuple[float, float, int]:
    """Calculate mean, sample stddev, and count for a sequence of numbers."""
    n = len(vals)
    if n == 0:
        return 0.0, 0.0, 0
    mean = sum(vals) / n
    if n == 1:
        return mean, 0.0, 1
    variance = sum((x - mean) ** 2 for x in vals) / (n - 1)
    std = math.sqrt(variance)
    return mean, std, n


def render_markdown(
    tracks: list[dict[str, Any]],
    results: dict[str, Any],
    scorecard: dict[str, Any],
    audio_dir: Path | str,
    label: str,
    settings: Any = None,
    unblind: bool = True,
) -> str:
    """Render comprehensive Markdown evaluation report."""
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    crit = scorecard.get("criteria", [])
    sc_name = scorecard.get("name", "Default Scorecard")
    tracks_eval = results.get("tracks", results)

    # Robust group and label resolvers (handles both baseline and blinded track dicts)
    def get_group(t: dict[str, Any]) -> str:
        if unblind:
            return str(t.get("raw_group") or t.get("group") or "")
        return str(t.get("group") or "")

    def get_group_label(t: dict[str, Any]) -> str:
        if unblind:
            return str(
                t.get("raw_group_label")
                or t.get("group_label")
                or t.get("raw_group")
                or t.get("group")
                or ""
            )
        return str(t.get("group_label") or t.get("group") or "")

    def get_track_label(t: dict[str, Any]) -> str:
        if unblind:
            return str(
                t.get("raw_label")
                or t.get("label")
                or t.get("stem")
                or t.get("name")
                or ""
            )
        return str(t.get("label") or t.get("stem") or t.get("name") or "")

    groups = sorted({get_group(t) for t in tracks if get_group(t)})
    group_labels = {
        get_group(t): (get_group_label(t) or get_group(t) or "root") for t in tracks
    }

    rating_criteria = [c for c in crit if c.get("type") == "rating"]
    choice_criteria = [
        c
        for c in crit
        if c.get("type") in ("choice", "multi_choice", "boolean", "radio")
    ]

    lines = [
        f"# Listening rating report — {label}",
        "",
        f"_Exported {now} by `rating_app`._",
        "",
        "## Setup",
        "",
        f"- **Scorecard**: {sc_name} (schema v{scorecard.get('schema_version', 1)})",
        f"- **Audio directory**: `{audio_dir}`",
        f"- **Tracks**: {len(tracks)}"
        + (f" across {len(groups)} folder arms" if groups else ""),
        "- **Criteria**: " + ", ".join(c.get("label", c.get("key", "")) for c in crit),
    ]

    if settings:
        blind_val = getattr(settings, "blind", False)
        blind_note = (
            "Yes (revealed in this report)" if blind_val and unblind else str(blind_val)
        )
        lines.append(f"- **Blind Mode**: {blind_note}")
        if getattr(settings, "report_columns", None):
            lines.append(
                f"- **Metadata Columns**: {', '.join(settings.report_columns)}"
            )

    lines += ["", "## Summary by Folder / Arm", ""]

    if not rating_criteria and not choice_criteria:
        lines.append("_(No rating or choice criteria defined)_")
    else:
        # 1. Rating metrics
        for rc in rating_criteria:
            rkey = rc["key"]
            rlabel = rc.get("label", rkey)
            arm_values: dict[str, list[float]] = {}
            for t in tracks:
                g = get_group(t)
                rec = tracks_eval.get(t.get("name", ""), {})
                val = _to_float(rec.get(rkey))
                if val is not None:
                    arm_values.setdefault(g, []).append(val)

            if arm_values:
                lines += [
                    f"### Metric: **{rlabel}** (1..{rc.get('max', 5)})",
                    "",
                    "| Folder / Arm | Mean | StdDev | N Scored |",
                    "|---|---|---|---|",
                ]
                stats_list = []
                for g, vals in arm_values.items():
                    m, s, n = _calc_stats(vals)
                    stats_list.append((g, m, s, n))
                stats_list.sort(key=lambda item: -item[1])

                for g, m, s, n in stats_list:
                    gdisplay = group_labels.get(g) or g or "root"
                    lines.append(f"| {gdisplay} | {m:.2f} | {s:.2f} | {n} |")
                lines.append("")

        # 2. Choice metrics distribution
        for cc in choice_criteria:
            ckey = cc["key"]
            clabel = cc.get("label", ckey)
            lines += [f"### Choice Distribution: **{clabel}**", ""]
            counts: dict[str, int] = {}
            total_answered = 0
            for t in tracks:
                rec = tracks_eval.get(t.get("name", ""), {})
                val = rec.get(ckey)
                if val is not None and val != "":
                    if isinstance(val, list):
                        for item in val:
                            counts[str(item)] = counts.get(str(item), 0) + 1
                            total_answered += 1
                    else:
                        counts[str(val)] = counts.get(str(val), 0) + 1
                        total_answered += 1

            if counts:
                lines += ["| Option | Count | % |", "|---|---|---|"]
                for opt, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                    pct = (count / total_answered) * 100 if total_answered else 0
                    lines.append(f"| {opt} | {count} | {pct:.1f}% |")
                lines.append("")
            else:
                lines += ["_(No responses recorded)_", ""]

    # Score table
    lines += ["## Score Table", ""]
    extra_cols = (
        list(getattr(settings, "report_columns", []))
        if settings and getattr(settings, "report_columns", None)
        else []
    )
    head = ["Folder/Arm", "Track", "Done"] + [c.get("label", c["key"]) for c in crit]
    if extra_cols:
        head += [f"Meta: {col}" for col in extra_cols]

    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "---|" * len(head))

    from rating_app.discovery import is_done

    for t in tracks:
        r = tracks_eval.get(t.get("name", ""), {})
        arm_disp = get_group_label(t) or "—"
        track_disp = get_track_label(t) or "—"
        done_flag = "Y" if is_done(r, crit) else "—"

        row = [str(arm_disp), str(track_disp), str(done_flag)]
        for c in crit:
            val = r.get(c["key"], "")
            if val is None:
                val = ""
            elif isinstance(val, list):
                val = ", ".join(str(x) for x in val)
            row.append(str(val))

        for col in extra_cols:
            val = t.get("metadata", {}).get(col, "")
            if val is None:
                val = ""
            row.append(str(val))

        lines.append("| " + " | ".join(row) + " |")

    # Per-track notes
    lines += ["", "## Per-Track Notes", ""]
    notes_crit = [c for c in crit if c.get("type") == "notes"]
    notes_keys = [c["key"] for c in notes_crit]
    has_notes = False

    for t in tracks:
        r = tracks_eval.get(t.get("name", ""), {})
        found_notes = []
        for nk in notes_keys:
            val = r.get(nk)
            if val is not None and str(val).strip():
                found_notes.append(f"{nk}: {val}")

        if found_notes:
            has_notes = True
            track_disp = get_track_label(t) or "—"
            tname = t.get("name", "")
            lines.append(f"### `{track_disp}` ({tname})")
            for fn in found_notes:
                lines.append(f"- {fn}")
            lines.append("")

    if not has_notes:
        lines += ["_(No notes recorded)_", ""]

    # Raw evaluation data
    lines += [
        "## Raw Evaluation Data",
        "",
        "```json",
        json.dumps(
            {"meta": results.get("meta", {}), "tracks": tracks_eval},
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def render_csv(
    tracks: list[dict[str, Any]],
    results: dict[str, Any],
    scorecard: dict[str, Any],
    settings: Any = None,
    unblind: bool = True,
) -> str:
    """Render evaluation results as CSV."""
    from rating_app.discovery import is_done

    crit = scorecard.get("criteria", [])
    tracks_eval = results.get("tracks", results)
    extra_cols = (
        list(getattr(settings, "report_columns", []))
        if settings and getattr(settings, "report_columns", None)
        else []
    )

    output = io.StringIO()
    writer = csv.writer(output)

    header = (
        ["file", "folder_arm", "track_label", "done"]
        + [c["key"] for c in crit]
        + extra_cols
    )
    writer.writerow(header)

    for t in tracks:
        r = tracks_eval.get(t.get("name", ""), {})
        if unblind:
            arm = t.get("raw_group") or t.get("group") or ""
            lbl = t.get("raw_label") or t.get("label") or t.get("stem") or ""
        else:
            arm = t.get("group") or ""
            lbl = t.get("label") or t.get("stem") or ""
        done = "1" if is_done(r, crit) else "0"

        row = [str(t.get("name", "")), str(arm), str(lbl), str(done)]
        for c in crit:
            val = r.get(c["key"], "")
            if val is None:
                val = ""
            elif isinstance(val, list):
                val = ";".join(str(x) for x in val)
            row.append(str(val))

        for col in extra_cols:
            val = t.get("metadata", {}).get(col, "")
            if val is None:
                val = ""
            row.append(str(val))

        writer.writerow(row)

    return output.getvalue()


def render_json(
    tracks: list[dict[str, Any]],
    results: dict[str, Any],
    scorecard: dict[str, Any],
    settings: Any = None,
    unblind: bool = True,
) -> dict[str, Any]:
    """Render evaluation results as structured JSON."""
    crit = scorecard.get("criteria", [])
    tracks_eval = results.get("tracks", results)

    rating_metrics = {}
    for rc in crit:
        if rc.get("type") == "rating":
            rkey = rc["key"]
            vals = []
            for t in tracks:
                rec = tracks_eval.get(t.get("name", ""), {})
                v = _to_float(rec.get(rkey))
                if v is not None:
                    vals.append(v)
            m, s, n = _calc_stats(vals)
            rating_metrics[rkey] = {"mean": round(m, 3), "std": round(s, 3), "n": n}

    return {
        "scorecard": scorecard.get("name"),
        "schema_version": scorecard.get("schema_version", 1),
        "exported_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "summary": {
            "total_tracks": len(tracks),
            "ratings": rating_metrics,
        },
        "tracks": tracks_eval,
    }
