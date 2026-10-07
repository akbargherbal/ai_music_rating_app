#!/usr/bin/env python3
"""rating_app — thin entry point: parse CLI → create_app()."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure rating_app package is in path when executed directly
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rating_app.cli import parse_args
from rating_app.settings import load_settings
from rating_app.web import create_app, pick_free_port


def main() -> int:
    cli_dict = parse_args()
    settings = load_settings(cli_args=cli_dict)

    # Prompt on interactive terminal if audio is unset
    if not settings.audio:
        if sys.stdin and sys.stdin.isatty():
            try:
                entered = input(
                    "Track directory (e.g. C:\\\\Users\\\\DELL\\\\Downloads\\\\jarir_lever_probe): "
                ).strip()
            except EOFError:
                entered = ""
            if entered:
                settings.audio = entered.strip('"').strip("'")
                settings.sources["audio"] = "interactive terminal prompt"
                if not cli_dict.get("label"):
                    settings.label = Path(settings.audio).name or "listening"

    if settings.audio and not Path(settings.audio).expanduser().is_dir():
        print(
            f"rating_app: warning — folder not found yet: {settings.audio} (you can set it on /setup)"
        )

    app = create_app(settings)

    port = (
        settings.port
        if settings.strict_port
        else pick_free_port(settings.host, settings.port)
    )
    print(f"rating_app: ready for '{settings.label}' [scorecard: {settings.scorecard}]")
    if not settings.strict_port and port != settings.port:
        print(f"  (port {settings.port} busy; using {port})")
    print(f"  open http://{settings.host}:{port}")

    app.run(host=settings.host, port=port, debug=settings.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
