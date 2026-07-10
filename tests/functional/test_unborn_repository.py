"""First-commit staging sessions in repositories with an unborn HEAD."""

from __future__ import annotations

import subprocess

import pytest

from .conftest import git_stage_batch


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def unborn_repo(tmp_path, monkeypatch):
    """Create a configured repository without any commits."""
    repo = tmp_path / "unborn"
    _git("init", str(repo))
    monkeypatch.chdir(repo)
    _git("config", "user.name", "Test User")
    _git("config", "user.email", "test@example.com")
    return repo


def test_start_include_and_abort_restore_unborn_branch(unborn_repo):
    """Abort should return a first-file session to its pre-commit state."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("one\ntwo\n")
    symbolic_head = _git("symbolic-ref", "HEAD").stdout.strip()

    git_stage_batch("start")
    git_stage_batch("include", "--file", "first.txt")
    assert _git("diff", "--cached", "--name-only").stdout == "first.txt\n"

    git_stage_batch("abort")

    assert _git("rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert _git("symbolic-ref", "HEAD").stdout.strip() == symbolic_head
    assert _git("ls-files").stdout == ""
    assert first_file.read_text() == "one\ntwo\n"
    assert _git("status", "--porcelain").stdout == "?? first.txt\n"


def test_abort_removes_first_commit_but_keeps_original_file(unborn_repo):
    """A commit created during the session should not make HEAD permanently born."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("first\n")
    git_stage_batch("start")
    git_stage_batch("include", "--file", "first.txt")
    _git("commit", "-m", "First commit")
    assert _git("rev-parse", "--verify", "HEAD").returncode == 0

    git_stage_batch("abort")

    assert _git("rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert first_file.read_text() == "first\n"
    assert _git("status", "--porcelain").stdout == "?? first.txt\n"


def test_stop_preserves_first_commit(unborn_repo):
    """Stopping should end the session without undoing a newly created HEAD."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("first\n")
    git_stage_batch("start")
    git_stage_batch("include", "--file", "first.txt")
    _git("commit", "-m", "First commit")
    first_commit = _git("rev-parse", "HEAD").stdout.strip()

    git_stage_batch("stop")

    assert _git("rev-parse", "HEAD").stdout.strip() == first_commit
    assert _git("status", "--porcelain").stdout == ""


def test_abort_preserves_unborn_file_kinds_and_ignored_paths(unborn_repo):
    """Abort should preserve original first-commit inputs without indexing them."""
    (unborn_repo / ".gitignore").write_text("ignored.bin\n")
    (unborn_repo / "script.sh").write_text("#!/bin/sh\nexit 0\n")
    (unborn_repo / "script.sh").chmod(0o755)
    (unborn_repo / "binary.bin").write_bytes(b"\x00\x01\xff")
    (unborn_repo / "empty.txt").write_bytes(b"")
    (unborn_repo / "ignored.bin").write_bytes(b"ignored")
    (unborn_repo / "target.txt").write_text("target\n")
    (unborn_repo / "link").symlink_to("target.txt")

    git_stage_batch("start")
    git_stage_batch("abort")

    assert _git("rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert _git("ls-files").stdout == ""
    assert (unborn_repo / "script.sh").stat().st_mode & 0o111
    assert (unborn_repo / "binary.bin").read_bytes() == b"\x00\x01\xff"
    assert (unborn_repo / "empty.txt").read_bytes() == b""
    assert (unborn_repo / "ignored.bin").read_bytes() == b"ignored"
    assert (unborn_repo / "link").is_symlink()


def test_unborn_intent_to_add_is_restored_on_abort(unborn_repo):
    """An existing intent-to-add entry should remain intent-to-add."""
    intent = unborn_repo / "intent.txt"
    intent.write_text("intent\n")
    _git("add", "-N", "intent.txt")

    git_stage_batch("start")
    git_stage_batch("abort")

    assert _git("status", "--porcelain").stdout == " A intent.txt\n"


def test_suggest_fixup_reports_unborn_history_boundary(unborn_repo):
    """History-dependent assistance should explain the missing first commit."""
    (unborn_repo / "first.txt").write_text("first\n")
    git_stage_batch("start")

    result = git_stage_batch("suggest-fixup", check=False)

    assert result.returncode != 0
    assert "requires at least one commit" in result.stderr
    assert "unborn" in result.stderr


def test_abort_restores_mixed_staged_and_untracked_start_state(unborn_repo):
    """Unborn abort should restore the exact initial index tree."""
    staged = unborn_repo / "staged.txt"
    staged.write_text("staged\n")
    _git("add", "staged.txt")
    review = unborn_repo / "review.txt"
    review.write_text("review\n")

    git_stage_batch("start")
    git_stage_batch("include", "--file", "review.txt")
    git_stage_batch("abort")

    assert _git("diff", "--cached", "--name-only").stdout == "staged.txt\n"
    assert _git("status", "--porcelain").stdout.splitlines() == [
        "A  staged.txt",
        "?? review.txt",
    ]


def test_unborn_line_include_can_be_undone(unborn_repo):
    """Line-level staging should create a restorable unborn checkpoint."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("one\ntwo\n")
    git_stage_batch("start")
    git_stage_batch("show", "--file", "first.txt")

    git_stage_batch("include", "--line", "1-2")
    assert _git("diff", "--cached", "--name-only").stdout == "first.txt\n"
    git_stage_batch("undo")

    assert _git("diff", "--cached", "--name-only").stdout == ""
    assert first_file.read_text() == "one\ntwo\n"


def test_session_continues_after_first_commit(unborn_repo):
    """A session started unborn should process later post-commit changes."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("first\n")
    git_stage_batch("start")
    git_stage_batch("include", "--file", "first.txt")
    _git("commit", "-m", "First commit")
    first_file.write_text("first\nsecond\n")

    git_stage_batch("start")
    git_stage_batch("include", "--file", "first.txt")
    git_stage_batch("stop")

    assert "+second" in _git("diff", "--cached").stdout


def test_unborn_session_can_store_first_file_in_batch(unborn_repo):
    """A no-history batch should accept first-commit file content."""
    first_file = unborn_repo / "first.txt"
    first_file.write_text("first\n")
    git_stage_batch("new", "first-batch")
    git_stage_batch("start")

    git_stage_batch("include", "--to", "first-batch", "--file", "first.txt")

    assert "first" in git_stage_batch("show", "--from", "first-batch").stdout
