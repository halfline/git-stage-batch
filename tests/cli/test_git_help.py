"""Tests for Git help integration."""

import os
from pathlib import Path
from unittest.mock import Mock

from git_stage_batch.cli import git_help


def test_build_manpath_with_existing_environment(monkeypatch):
    """Existing MANPATH should be preserved after the packaged root."""
    monkeypatch.setattr(git_help, "_resolve_default_manpath", lambda: "/ignored")

    result = git_help._build_manpath_with_packaged_page(
        Path("/tmp/pkg-man"),
        {"MANPATH": "/custom/man"},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}/custom/man"


def test_build_manpath_uses_computed_default(monkeypatch):
    """Unset MANPATH should use the computed default search path."""
    monkeypatch.setattr(
        git_help,
        "_resolve_default_manpath",
        lambda: "/usr/share/man:/usr/local/share/man",
    )

    result = git_help._build_manpath_with_packaged_page(
        Path("/tmp/pkg-man"),
        {},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}/usr/share/man:/usr/local/share/man"


def test_build_manpath_falls_back_to_double_colon(monkeypatch):
    """Unset MANPATH should preserve default lookup semantics as a fallback."""
    monkeypatch.setattr(git_help, "_resolve_default_manpath", lambda: None)

    result = git_help._build_manpath_with_packaged_page(
        Path("/tmp/pkg-man"),
        {},
    )

    assert result == f"/tmp/pkg-man{os.pathsep}{os.pathsep}"


def test_resolve_default_manpath_unsets_manpath(monkeypatch):
    """Default manpath discovery should ignore the current MANPATH override."""
    captured_env = {}

    def fake_run(*_args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs["env"]
        return Mock(returncode=0, stdout="/usr/share/man\n")

    monkeypatch.setattr(git_help, "run_command", fake_run)
    monkeypatch.setenv("MANPATH", "/custom/man")

    result = git_help._resolve_default_manpath()

    assert result == "/usr/share/man"
    assert "MANPATH" not in captured_env


def test_show_git_stage_batch_help_uses_packaged_page_first(monkeypatch, tmp_path):
    """Packaged man pages should be added to MANPATH before invoking git help."""
    manpage = tmp_path / "assets" / "man" / "man1" / "git-stage-batch.1"
    manpage.parent.mkdir(parents=True)
    manpage.write_text("test", encoding="utf-8")

    monkeypatch.setattr(
        git_help.resources,
        "files",
        lambda _package: tmp_path,
    )

    class _AsFile:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self.path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(git_help.resources, "as_file", lambda path: _AsFile(path))
    monkeypatch.setattr(git_help, "_resolve_default_manpath", lambda: "/usr/share/man")

    calls = []

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return Mock(returncode=0)

    monkeypatch.setattr(git_help, "run_git_command", fake_git)

    assert git_help._show_git_stage_batch_help() is True
    assert calls[0][0] == ["help", "git-stage-batch"]
    assert calls[0][1]["MANPATH"] == (
        f"{manpage.parent.parent}{os.pathsep}/usr/share/man"
    )


def test_show_git_stage_batch_help_uses_packaged_command_page(monkeypatch, tmp_path):
    """Command help should prefer the matching packaged man page."""
    manpage = tmp_path / "assets" / "man" / "man1" / "git-stage-batch-include.1"
    manpage.parent.mkdir(parents=True)
    manpage.write_text("test", encoding="utf-8")

    monkeypatch.setattr(
        git_help.resources,
        "files",
        lambda _package: tmp_path,
    )

    class _AsFile:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self.path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(git_help.resources, "as_file", lambda path: _AsFile(path))
    monkeypatch.setattr(git_help, "_resolve_default_manpath", lambda: "/usr/share/man")

    calls = []

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        return Mock(returncode=0)

    monkeypatch.setattr(git_help, "run_git_command", fake_git)

    assert git_help._show_git_stage_batch_help("stage-batch-include") is True
    assert calls[0][0] == ["help", "git-stage-batch-include"]
    assert calls[0][1]["MANPATH"] == (
        f"{manpage.parent.parent}{os.pathsep}/usr/share/man"
    )


def test_show_git_stage_batch_help_materializes_editable_manpage(
    monkeypatch,
    tmp_path,
):
    """Editable installs should copy the man page into a real man1 tree."""
    editable_manpage = tmp_path / "build" / "cp313" / "git-stage-batch.1"
    editable_manpage.parent.mkdir(parents=True)
    editable_manpage.write_text("test", encoding="utf-8")

    class _EditableRoot:
        def joinpath(self, *segments):
            assert segments == ("assets", "man", "man1", "git-stage-batch.1")
            return editable_manpage

    monkeypatch.setattr(git_help.resources, "files", lambda _package: _EditableRoot())

    class _AsFile:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self.path

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(git_help.resources, "as_file", lambda path: _AsFile(path))
    monkeypatch.setattr(git_help, "_resolve_default_manpath", lambda: "/usr/share/man")

    calls = []

    def fake_git(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env")))
        manpath_root = kwargs["env"]["MANPATH"].split(os.pathsep, 1)[0]
        staged_manpage = Path(manpath_root) / "man1" / "git-stage-batch.1"
        assert staged_manpage.exists()
        assert staged_manpage.read_text(encoding="utf-8") == "test"
        return Mock(returncode=0)

    monkeypatch.setattr(git_help, "run_git_command", fake_git)

    assert git_help._show_git_stage_batch_help() is True
    assert calls[0][1]["MANPATH"].endswith(f"{os.pathsep}/usr/share/man")
