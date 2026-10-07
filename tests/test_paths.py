"""rating_app.paths - app-root resolution and safe directory browsing."""

from __future__ import annotations

from pathlib import Path

import pytest

import rating_app.paths as paths
from rating_app.paths import (
    app_path,
    browse_directory,
    is_loopback_host,
    is_path_within,
    resolve_safe_path,
)


# ------------------------------------------------------------------ small helpers
def test_app_path_relative_is_anchored_to_app_root(app_root):
    assert app_path("runs/x") == app_root / "runs" / "x"


def test_app_path_absolute_is_unchanged(tmp_path):
    assert app_path(tmp_path) == tmp_path


def test_app_path_expands_user():
    assert app_path("~/thing") == Path.home() / "thing"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "", None])
def test_loopback_hosts(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_non_loopback_hosts(host):
    assert is_loopback_host(host) is False


def test_resolve_safe_path_resolves_symlinks_and_dots(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolve_safe_path(tmp_path / "a" / ".." / "a") == (tmp_path / "a").resolve()


def test_is_path_within(tmp_path):
    root = tmp_path.resolve()
    sub = root / "sub"
    sub.mkdir()
    assert is_path_within(sub, root)
    assert is_path_within(root, root)
    assert not is_path_within(root.parent, root)
    assert not is_path_within(root / ".." / "elsewhere", root)


def test_is_path_within_returns_false_on_os_error(tmp_path, monkeypatch):
    def boom(self, *a, **kw):
        raise OSError("symlink loop")

    monkeypatch.setattr(Path, "resolve", boom)
    assert is_path_within(tmp_path, tmp_path) is False


# ------------------------------------------------------------------ browse_directory
@pytest.fixture()
def tree(tmp_path) -> Path:
    root = (tmp_path / "tree").resolve()
    (root / "Zeta").mkdir(parents=True)
    (root / "alpha").mkdir()
    (root / ".hidden").mkdir()
    (root / "b.json").write_text("{}")
    (root / "a.WAV").write_bytes(b"x")
    (root / ".secret.json").write_text("{}")
    return root


def test_browse_lists_dirs_first_case_insensitive_and_hides_dotfiles(tree):
    res = browse_directory(str(tree))
    assert res["current"] == str(tree)
    assert [d["name"] for d in res["dirs"]] == ["alpha", "Zeta"]
    assert [f["name"] for f in res["files"]] == ["a.WAV", "b.json"]
    assert res["parent"] == str(tree.parent)


def test_browse_ignores_entries_that_are_neither_file_nor_dir(tree):
    """A dangling symlink is neither; it must not crash the listing or show up."""
    try:
        (tree / "dangling").symlink_to(tree / "does-not-exist")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    res = browse_directory(str(tree))
    assert "dangling" not in [e["name"] for e in res["dirs"] + res["files"]]


def test_browse_extension_filter_is_case_insensitive(tree):
    res = browse_directory(str(tree), allowed_extensions=(".json",))
    assert [f["name"] for f in res["files"]] == ["b.json"]
    res = browse_directory(str(tree), allowed_extensions=(".wav",))
    assert [f["name"] for f in res["files"]] == ["a.WAV"]


def test_browse_defaults_to_cwd_then_to_first_allowed_root(tree, monkeypatch):
    monkeypatch.chdir(tree)
    assert browse_directory(None)["current"] == str(tree)
    assert browse_directory("", allowed_roots=[tree / "alpha"])["current"] == str(tree / "alpha")


def test_browse_outside_allowed_roots_snaps_back_to_root(tree, tmp_path):
    res = browse_directory(str(tmp_path), allowed_roots=[tree])
    assert res["current"] == str(tree)


def test_browse_hides_parent_when_it_is_outside_allowed_roots(tree):
    res = browse_directory(str(tree), allowed_roots=[tree])
    assert res["parent"] is None
    inside = browse_directory(str(tree / "alpha"), allowed_roots=[tree])
    assert inside["parent"] == str(tree)


def test_browse_file_path_falls_back_to_parent_directory(tree):
    assert browse_directory(str(tree / "b.json"))["current"] == str(tree)


def test_browse_nonexistent_path_falls_back_to_existing_ancestor(tree):
    assert browse_directory(str(tree / "ghost"))["current"] == str(tree)


def test_browse_unreachable_target_falls_back_to_cwd(tree, monkeypatch):
    """Neither the path nor its parent is a directory -> cwd."""
    monkeypatch.chdir(tree)
    assert browse_directory(str(tree / "ghost" / "deeper"))["current"] == str(tree)


def test_browse_filesystem_root_has_no_parent():
    assert browse_directory("/")["parent"] is None


def test_browse_swallows_permission_errors(tree, monkeypatch):
    def deny(self):
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "iterdir", deny)
    res = browse_directory(str(tree))
    assert res["dirs"] == [] and res["files"] == []


def test_module_constants_point_into_sandbox(app_root):
    assert paths.APP_ROOT == app_root
    assert paths.SCORECARD_DIR == app_root / "scorecards"
