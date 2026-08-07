"""Tests for literal file-scoped change-status queries."""

from __future__ import annotations

import subprocess

import pytest

from git_stage_batch.data.file_change_status import (
    file_has_staged_changes,
    file_has_unstaged_changes,
)


@pytest.fixture
def status_repo(tmp_path, monkeypatch):
    """Create a repository for literal pathspec status checks."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    return tmp_path


@pytest.mark.parametrize(
    ("file_path", "pathspec_decoy"),
    (
        (":(top)foo", "foo"),
        (":(glob)foo", "foo"),
        ("*.c", "other.c"),
        (":(exclude)foo", "other.txt"),
    ),
)
def test_file_change_status_treats_repository_paths_literally(
    status_repo,
    file_path,
    pathspec_decoy,
):
    """A repository filename must never become a Git pathspec expression."""
    literal_file = status_repo / file_path
    decoy_file = status_repo / pathspec_decoy
    literal_file.write_text("literal base\n")
    decoy_file.write_text("decoy base\n")
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", file_path, pathspec_decoy],
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "base"], check=True)

    decoy_file.write_text("decoy changed\n")
    assert not file_has_unstaged_changes(file_path)
    decoy_file.write_text("decoy base\n")

    literal_file.write_text("literal changed\n")
    assert file_has_unstaged_changes(file_path)
    subprocess.run(["git", "reset", "--hard", "-q", "HEAD"], check=True)

    decoy_file.write_text("decoy staged\n")
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", pathspec_decoy],
        check=True,
    )
    assert not file_has_staged_changes(file_path)
    subprocess.run(["git", "reset", "--hard", "-q", "HEAD"], check=True)

    literal_file.write_text("literal staged\n")
    subprocess.run(
        ["git", "--literal-pathspecs", "add", "--", file_path],
        check=True,
    )
    assert file_has_staged_changes(file_path)


@pytest.mark.parametrize(
    "query",
    (file_has_staged_changes, file_has_unstaged_changes),
)
def test_file_change_status_propagates_git_errors(monkeypatch, query):
    """A failed quiet diff must not masquerade as an unchanged file."""
    from git_stage_batch.data import file_change_status

    monkeypatch.setattr(
        file_change_status,
        "run_git_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["git", "diff", "--quiet"],
            128,
            stdout="",
            stderr="fatal: malformed index",
        ),
    )

    with pytest.raises(subprocess.CalledProcessError) as error:
        query("file.txt")

    assert error.value.stderr == "fatal: malformed index"
