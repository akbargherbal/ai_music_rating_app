"""rating_app.discovery — track discovery, metadata extraction, grouping, and blinding."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Sequence, Any

from rating_app.scorecard import show_if_met

DEFAULT_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus")


def load_json_safe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def extract_folder_metadata(folder: Path) -> tuple[str, dict[str, Any]]:
    """Extract folder label and rich metadata dictionary from sidecar JSON files."""
    meta: dict[str, Any] = {}
    label = ""
    for fn in ("_knob.json", "_meta.json"):
        p = folder / fn
        if p.is_file():
            data = load_json_safe(p)
            meta.update(data)
            for k in ("label", "name", "variant", "arm"):
                if str(data.get(k, "")).strip():
                    label = str(data[k]).strip()
                    break
            if not label:
                cfg = str(data.get("config", "")).strip()
                opts = str(data.get("extra_request_opts", data.get("opts", ""))).strip()
                if opts:
                    label = f"{cfg}: {opts}" if cfg else opts
                elif cfg:
                    label = cfg
            if label:
                break
    return label, meta


def extract_track_metadata(audio_file: Path) -> dict[str, Any]:
    """Extract track-specific metadata from <track>.json or <track_stem>.json sidecars."""
    meta: dict[str, Any] = {}
    candidates = [
        audio_file.with_suffix(".json"),
        audio_file.parent / f"{audio_file.stem}.json",
    ]
    for c in candidates:
        if c.is_file() and c != audio_file:
            meta.update(load_json_safe(c))
    return meta


def load_file_labels(audio_dir: Path) -> dict[str, str]:
    p = audio_dir / "labels.json"
    if not p.is_file():
        return {}
    data = load_json_safe(p)
    return {str(k): str(v) for k, v in data.items()}


def discover(
    audio_dir: Path | str | None,
    audio_exts: Sequence[str] = DEFAULT_AUDIO_EXTS,
    group_by: str = "parent",
    blind: bool = False,
    blind_seed: int | None = None,
) -> list[dict[str, Any]]:
    """Discover audio files under audio_dir, attaching metadata and applying blind mode."""
    if not audio_dir:
        return []
    root = Path(audio_dir).expanduser().resolve()
    if not root.is_dir():
        return []

    flabels = load_file_labels(root)
    valid_exts = tuple(ext.lower() for ext in audio_exts)
    tracks: list[dict[str, Any]] = []

    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in valid_exts:
            continue

        rel = f.relative_to(root)
        posix_rel = rel.as_posix()

        # Grouping
        if group_by == "none":
            group = ""
        elif group_by == "filename":
            group = f.stem.split("_")[0] if "_" in f.stem else ""
        else:  # parent
            group = "" if rel.parent == Path(".") else rel.parent.as_posix()

        group_label, folder_meta = (
            extract_folder_metadata(root / group) if group else ("", {})
        )
        track_meta = extract_track_metadata(f)

        merged_meta = dict(folder_meta)
        merged_meta.update(track_meta)

        raw_label = str(flabels.get(posix_rel) or flabels.get(f.name) or f.stem)

        tracks.append(
            {
                "name": posix_rel,
                "id": posix_rel,
                "file": posix_rel,
                "stem": f.stem,
                "group": group,
                "group_label": group_label,
                "label": raw_label,
                "raw_label": raw_label,
                "raw_group": group,
                "raw_group_label": group_label,
                "metadata": merged_meta,
                "raw_metadata": merged_meta,
            }
        )

    # Sort deterministically
    tracks.sort(key=lambda t: (t["group"], t["stem"]))

    # Blinding logic
    if blind:
        rng = random.Random(blind_seed if blind_seed is not None else 42)
        rng.shuffle(tracks)
        for idx, t in enumerate(tracks, start=1):
            t["id"] = f"b{idx:02d}"  # opaque: URLs must not reveal arm or filename
            t["label"] = f"Track {idx:02d}"
            t["group"] = "blinded"
            t["group_label"] = "Blind Evaluation"
            t["metadata"] = {}  # generation settings would unblind the arm

    return tracks


def sections_of(tracks: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group tracks into ordered sections for display."""
    out: list[tuple[str, list[dict[str, Any]]]] = []
    index: dict[str, int] = {}
    for t in tracks:
        g = t["group"]
        if g not in index:
            index[g] = len(out)
            label = t["group_label"] or g or "Tracks"
            out.append((label, []))
        out[index[g]][1].append(t)
    return out


def is_done(rec: dict[str, Any] | None, criteria: list[dict[str, Any]]) -> bool:
    """Determine whether a track evaluation record is complete according to criteria."""
    if not rec:
        return False
    for c in criteria:
        if not show_if_met(c, rec):
            continue
        if c.get("required", True):
            val = rec.get(c["key"])
            if val is None or str(val).strip() == "":
                return False
            if (
                c.get("type") == "multi_choice"
                and isinstance(val, list)
                and len(val) == 0
            ):
                return False
    return True
