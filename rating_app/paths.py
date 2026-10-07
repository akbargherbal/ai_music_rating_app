"""rating_app.paths — safe server-side directory browsing and path sanitization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence


def resolve_safe_path(path_str: str | Path) -> Path:
    """Resolve and expand a path safely."""
    p = Path(path_str).expanduser()
    return p.resolve()


def is_path_within(target: Path, root: Path) -> bool:
    """Check if target path is within or equal to root."""
    try:
        resolved_target = target.resolve()
        resolved_root = root.resolve()
        return (
            resolved_target == resolved_root or resolved_root in resolved_target.parents
        )
    except (OSError, RuntimeError):
        return False


def browse_directory(
    target_str: str | None = None,
    allowed_roots: Sequence[Path] | None = None,
    allowed_extensions: Sequence[str] | None = None,
) -> dict:
    """Browse a server-side directory safely, returning subfolders and files.

    If target_str is None or empty, starts at Path.cwd() or the first allowed_root.
    """
    if target_str:
        target = Path(target_str).expanduser().resolve()
    else:
        target = allowed_roots[0].resolve() if allowed_roots else Path.cwd().resolve()

    if allowed_roots:
        is_safe = any(is_path_within(target, r) for r in allowed_roots)
        if not is_safe:
            target = allowed_roots[0].resolve()

    if not target.is_dir():
        target = target.parent if target.parent.is_dir() else Path.cwd().resolve()

    parent_dir = str(target.parent) if target.parent != target else None
    if allowed_roots and parent_dir:
        if not any(is_path_within(target.parent, r) for r in allowed_roots):
            parent_dir = None

    dirs = []
    files = []

    try:
        for entry in sorted(
            target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
        ):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry.resolve())})
            elif entry.is_file():
                if allowed_extensions:
                    if entry.suffix.lower() in allowed_extensions:
                        files.append({"name": entry.name, "path": str(entry.resolve())})
                else:
                    files.append({"name": entry.name, "path": str(entry.resolve())})
    except (PermissionError, OSError):
        pass

    return {
        "current": str(target),
        "parent": parent_dir,
        "dirs": dirs,
        "files": files,
    }
