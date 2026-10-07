"""rating_app.cli — command-line argument parsing."""

from __future__ import annotations

import argparse
from typing import Sequence


def get_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rating_app",
        description="General listening rating application with adaptable scorecards.",
    )
    ap.add_argument(
        "--audio",
        default=None,
        help="Folder containing rendered audio variants (prompted if omitted).",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Directory for legacy files (evaluations.json / criteria.json are imported from here). Results live in runs/<run_id>/.",
    )
    ap.add_argument(
        "--label",
        default=None,
        help="Run label (defaults to audio folder name or 'listening').",
    )
    ap.add_argument(
        "--scorecard",
        default=None,
        help="Scorecard JSON path or preset name from scorecards/ library.",
    )
    ap.add_argument(
        "--fields",
        default=None,
        help="[Deprecated alias for --scorecard] Scorecard JSON path.",
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Path or preset name for settings configuration JSON.",
    )
    ap.add_argument(
        "--run-id",
        default=None,
        help="Explicit identifier for the evaluation run.",
    )
    ap.add_argument(
        "--host",
        default=None,
        help="Host interface to bind (default: 127.0.0.1).",
    )
    ap.add_argument(
        "--port",
        type=int,
        default=None,
        help="Preferred port (default: 5000; scans upward if busy).",
    )
    ap.add_argument(
        "--strict-port",
        action="store_true",
        default=None,
        help="Fail if preferred port is busy instead of scanning upward.",
    )
    ap.add_argument(
        "--blind",
        action="store_true",
        default=None,
        help="Enable blind mode: anonymize track labels and shuffle order.",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        default=None,
        help="Run Flask in debug mode.",
    )
    return ap


def parse_args(args: Sequence[str] | None = None) -> dict:
    """Parse CLI arguments into a clean dictionary filtering unset options."""
    parser = get_parser()
    parsed = parser.parse_args(args)
    return {k: v for k, v in vars(parsed).items() if v is not None}
