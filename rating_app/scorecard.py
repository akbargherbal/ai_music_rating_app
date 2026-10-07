"""rating_app.scorecard — load, validate, and normalize scorecard questions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rating_app.paths import SCORECARD_DIR

SUPPORTED_TYPES = {
    "rating",
    "choice",
    "radio",
    "multi_choice",
    "boolean",
    "number",
    "notes",
}

FALLBACK_CRITERIA = [
    {
        "key": "overall",
        "label": "Overall quality",
        "type": "rating",
        "max": 5,
        "help": "Your gut call on the whole take.",
    },
    {
        "key": "notes",
        "label": "Notes",
        "type": "notes",
        "required": False,
        "help": "Anything worth noting.",
    },
]

DEFAULT_SCORECARD = {
    "schema_version": 1,
    "name": "Default (Audio Evaluation)",
    "description": "General-purpose audio listening rating scorecard",
    "summary_metric": "overall",
    "criteria": FALLBACK_CRITERIA,
}


def norm_options(opts: Any) -> list[dict[str, str]]:
    """Normalize options list into standard [{"v": str, "t": str}] format."""
    out = []
    for o in opts or []:
        if isinstance(o, dict):
            v = str(o.get("v", o.get("value", "")))
            t = str(o.get("t", o.get("label", v)))
            out.append({"v": v, "t": t})
        else:
            out.append({"v": str(o), "t": str(o)})
    return out


def validate_scorecard(data: Any) -> list[str]:
    """Validate a scorecard definition.

    Returns a list of human-readable error messages. If empty, the scorecard is valid.
    """
    errors: list[str] = []
    if not isinstance(data, (dict, list)):
        return ["Scorecard must be a JSON object or list of criteria."]

    if isinstance(data, list):
        crit_list = data
        summary_metric = None
    else:
        crit_list = data.get("criteria")
        summary_metric = data.get("summary_metric")
        if "schema_version" in data and not isinstance(data["schema_version"], int):
            errors.append("`schema_version` must be an integer.")

    if not isinstance(crit_list, list) or len(crit_list) == 0:
        errors.append("Scorecard must contain a non-empty `criteria` list.")
        return errors

    seen_keys: set[str] = set()
    criteria_keys: set[str] = set()
    rating_keys: set[str] = set()

    for idx, c in enumerate(crit_list):
        prefix = f"Criterion #{idx + 1}"
        if not isinstance(c, dict):
            errors.append(f"{prefix} must be an object.")
            continue

        k = str(c.get("key", "")).strip()
        if not k:
            errors.append(f"{prefix} missing required field `key`.")
            continue
        if k in seen_keys:
            errors.append(f"Duplicate criterion key: '{k}'.")
        seen_keys.add(k)
        criteria_keys.add(k)

        ctype = str(c.get("type", "rating")).lower()
        if ctype not in SUPPORTED_TYPES:
            errors.append(
                f"Criterion '{k}' has unsupported type '{ctype}'. Supported: {', '.join(sorted(SUPPORTED_TYPES))}."
            )

        if ctype == "rating":
            rating_keys.add(k)
            max_val = c.get("max", 5)
            if not isinstance(max_val, int) or max_val < 2:
                errors.append(f"Criterion '{k}' rating `max` must be an integer >= 2.")
            if "scale_labels" in c and not isinstance(c["scale_labels"], (dict, list)):
                errors.append(f"Criterion '{k}' `scale_labels` must be a dict or list.")

        elif ctype in ("choice", "radio", "multi_choice"):
            opts = c.get("options")
            if not isinstance(opts, list) or len(opts) == 0:
                errors.append(f"Criterion '{k}' ({ctype}) requires non-empty `options` list.")

        if "show_if" in c:
            cond = c.get("show_if")
            if not isinstance(cond, dict) or "key" not in cond:
                errors.append(f"Criterion '{k}' `show_if` must be an object with at least a `key` field.")

    if summary_metric:
        sm = str(summary_metric).strip()
        if sm not in criteria_keys:
            errors.append(f"`summary_metric` '{sm}' does not match any criterion key.")
        elif sm not in rating_keys:
            errors.append(f"`summary_metric` '{sm}' must refer to a criterion of type 'rating'.")

    return errors


def show_if_met(criterion: dict, rec: dict | None) -> bool:
    """Server-side mirror of the browser `show_if` rule (eq/neq/lt/lte/gt/gte)."""
    cond = criterion.get("show_if")
    if not cond:
        return True
    val = (rec or {}).get(cond.get("key"))
    if val is None or val == "":
        return True  # controlling question unanswered: browser shows the field too
    try:
        num = float(val)
    except (TypeError, ValueError):
        num = None
    ok = True
    if "eq" in cond:
        ok = ok and str(val) == str(cond["eq"])
    if "neq" in cond:
        ok = ok and str(val) != str(cond["neq"])
    for op, fn in (("lt", lambda a, b: a < b), ("lte", lambda a, b: a <= b),
                   ("gt", lambda a, b: a > b), ("gte", lambda a, b: a >= b)):
        if op in cond:
            ok = ok and num is not None and fn(num, float(cond[op]))
    return ok


def normalize_criteria(crit: list[dict]) -> list[dict]:
    """Normalize criteria list to canonical format with default values."""
    out = []
    for c in crit:
        c = dict(c)
        ctype = str(c.get("type", "rating")).lower()
        if ctype == "radio":
            ctype = "choice"
        c["type"] = ctype
        c.setdefault("label", c.get("key", "?"))
        c.setdefault("required", c["type"] not in ("notes", "multi_choice"))
        c.setdefault("help", "")

        if c["type"] == "rating":
            c.setdefault("max", 5)
            sl = c.get("scale_labels")
            if isinstance(sl, dict):
                c["scale_labels"] = {str(k): str(v) for k, v in sl.items()}
            elif isinstance(sl, list):
                c["scale_labels"] = {str(i + 1): str(v) for i, v in enumerate(sl)}
        elif c["type"] in ("choice", "multi_choice"):
            c["options"] = norm_options(c.get("options"))
        elif c["type"] == "boolean":
            c.setdefault("options", [{"v": "true", "t": "Yes"}, {"v": "false", "t": "No"}])
        out.append(c)
    return out


def normalize_scorecard(data: Any) -> dict:
    """Normalize raw dict or list into complete scorecard structure."""
    if isinstance(data, list):
        data = {"criteria": data}
    data = dict(data)

    criteria = normalize_criteria(data.get("criteria") or FALLBACK_CRITERIA)
    rating_keys = [c["key"] for c in criteria if c["type"] == "rating"]

    summary_metric = data.get("summary_metric")
    if not summary_metric or summary_metric not in rating_keys:
        summary_metric = rating_keys[0] if rating_keys else None

    return {
        "schema_version": int(data.get("schema_version", 1)),
        "name": str(data.get("name", "Scorecard")),
        "description": str(data.get("description", "")),
        "summary_metric": summary_metric,
        "sections": data.get("sections") or [],
        "criteria": criteria,
    }


def load_scorecard(path_or_dict: str | Path | dict | list | None, fallback_default: bool = True) -> dict:
    """Load and validate a scorecard from path or dict.

    Raises ValueError with formatted validation errors if the file is invalid.
    """
    if path_or_dict is None:
        if fallback_default:
            p = SCORECARD_DIR / "default.json"
            if p.is_file():
                return load_scorecard(p, fallback_default=False)
            import warnings
            warnings.warn(f"{p} not found; using the minimal built-in fallback scorecard.")
            return normalize_scorecard(DEFAULT_SCORECARD)
        raise ValueError("No scorecard provided.")

    if isinstance(path_or_dict, (dict, list)):
        errs = validate_scorecard(path_or_dict)
        if errs:
            raise ValueError(f"Scorecard validation failed: {'; '.join(errs)}")
        return normalize_scorecard(path_or_dict)

    path = Path(path_or_dict).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Scorecard file not found: {path}")

    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in scorecard file '{path}': {e}") from e
    except OSError as e:
        raise ValueError(f"Cannot read scorecard file '{path}': {e}") from e

    errs = validate_scorecard(data)
    if errs:
        raise ValueError(f"Scorecard validation failed for '{path}': {'; '.join(errs)}")

    normalized = normalize_scorecard(data)
    normalized["path"] = str(path.resolve())
    return normalized


def list_scorecards(search_dirs: list[str | Path]) -> list[dict]:
    """Scan directories for valid scorecard JSON files."""
    results = []
    seen_paths = set()
    for d in search_dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        for f in sorted(p.glob("*.json")):
            resolved = f.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                sc = load_scorecard(f, fallback_default=False)
                results.append({
                    "id": f.stem,
                    "filename": f.name,
                    "path": str(resolved),
                    "name": sc.get("name") or f.stem,
                    "description": sc.get("description", ""),
                    "summary_metric": sc.get("summary_metric"),
                    "num_criteria": len(sc.get("criteria", [])),
                })
            except Exception:
                continue
    return results