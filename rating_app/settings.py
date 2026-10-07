"""rating_app.settings — layered settings management with source tracking."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from rating_app.paths import CONFIG_DIR

ENV_PREFIX = "RATING_"

DEFAULT_AUDIO_EXTS = [".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus"]
DEFAULT_SCORECARD_DIRS = ["scorecards", "."]
DEFAULT_CONFIG_DIRS = ["configs"]


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 5000
    strict_port: bool = False
    debug: bool = False
    audio: str = ""
    label: str = "listening"
    out: str = "./rating_out"
    scorecard: str = "default"
    config: str = ""
    runs_dir: str = "runs"
    scorecard_dirs: list[str] = field(
        default_factory=lambda: list(DEFAULT_SCORECARD_DIRS)
    )
    config_dirs: list[str] = field(default_factory=lambda: list(DEFAULT_CONFIG_DIRS))
    audio_exts: list[str] = field(default_factory=lambda: list(DEFAULT_AUDIO_EXTS))
    group_by: str = "parent"  # "parent", "filename", "none"
    blind: bool = False
    autosave: bool = False
    save_and_advance: bool = False
    report_columns: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def _parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    return s in ("1", "true", "yes", "on")


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_settings(
    cli_args: dict[str, Any] | None = None,
    audio_path_override: str | Path | None = None,
    config_file_override: str | Path | None = None,
) -> Settings:
    """Load settings according to strict layered precedence rule:

    CLI flag > env var > explicit config file > <audio>/_rating.json > user-level config > built-in defaults.
    """
    settings = Settings()
    sources: dict[str, str] = {
        k: "default" for k in asdict(settings).keys() if k != "sources"
    }

    def apply_layer(layer_data: dict[str, Any], layer_source: str):
        for k, v in layer_data.items():
            if v is None:
                continue
            if hasattr(settings, k):
                default_val = getattr(settings, k)
                if isinstance(default_val, bool):
                    v = _parse_bool(v)
                elif isinstance(default_val, int) and not isinstance(v, bool):
                    try:
                        v = int(v)
                    except ValueError:
                        continue
                elif isinstance(default_val, list) and isinstance(v, str):
                    v = [item.strip() for item in v.split(",") if item.strip()]

                setattr(settings, k, v)
                sources[k] = layer_source

    # 1. User-level config ~/.config/rating_app/config.json
    user_conf = Path.home() / ".config" / "rating_app" / "config.json"
    if user_conf.is_file():
        apply_layer(_read_json_file(user_conf), str(user_conf))

    # 2. Determine potential audio path early for <audio>/_rating.json
    early_audio = audio_path_override
    if not early_audio and cli_args and cli_args.get("audio"):
        early_audio = cli_args["audio"]
    if not early_audio and os.getenv(f"{ENV_PREFIX}AUDIO"):
        early_audio = os.getenv(f"{ENV_PREFIX}AUDIO")

    if early_audio:
        audio_dir = Path(early_audio).expanduser()
        audio_conf = audio_dir / "_rating.json"
        if audio_conf.is_file():
            apply_layer(_read_json_file(audio_conf), f"{audio_conf}")

    # 3. Explicit config file (--config or override)
    explicit_cfg = config_file_override
    if not explicit_cfg and cli_args and cli_args.get("config"):
        explicit_cfg = cli_args["config"]
    if not explicit_cfg and os.getenv(f"{ENV_PREFIX}CONFIG"):
        explicit_cfg = os.getenv(f"{ENV_PREFIX}CONFIG")

    if explicit_cfg:
        cfg_path = Path(explicit_cfg).expanduser()
        if not cfg_path.is_file():
            candidate = CONFIG_DIR / f"{explicit_cfg}.json"
            if candidate.is_file():
                cfg_path = candidate
        if not cfg_path.is_file():
            raise FileNotFoundError(f"Config file not found: {explicit_cfg}")
        try:
            cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"Cannot read config file '{cfg_path}': {e}") from e
        if not isinstance(cfg_data, dict):
            raise ValueError(f"Config file '{cfg_path}' must contain a JSON object.")
        apply_layer(cfg_data, f"config file: {cfg_path}")

    # 4. Environment variables RATING_*
    env_data: dict[str, Any] = {}
    for k in asdict(settings).keys():
        env_var = f"{ENV_PREFIX}{k.upper()}"
        if env_var in os.environ:
            env_data[k] = os.environ[env_var]
    apply_layer(env_data, "environment variable")

    # 5. CLI args
    if cli_args:
        cli_clean = {}
        for k, v in cli_args.items():
            if v is not None:
                if k == "fields" and v:
                    cli_clean["scorecard"] = v
                else:
                    cli_clean[k] = v
        apply_layer(cli_clean, "CLI argument")

    # Label resolution fallback
    if not settings.label or settings.label == "listening":
        if settings.audio:
            settings.label = Path(settings.audio).name or "listening"
            if sources["label"] == "default":
                sources["label"] = f"inferred from audio folder: {settings.label}"

    settings.sources = sources
    return settings
