"""Functional tests for undo support."""

import subprocess

from .conftest import git_stage_batch, get_staged_diff


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _commit_file(path, content: str) -> None:
    path.write_text(content)
    _git("add", str(path))
    _git("commit", "-m", f"Add {path.name}")


def test_undo_skip_restores_selected_hunk(repo_with_changes):
    """Undoing skip makes the skipped hunk selected again."""
    git_stage_batch("start")
    first_show = git_stage_batch("show").stdout

    git_stage_batch("skip")
    after_skip = git_stage_batch("show", check=False)
    if after_skip.returncode == 0:
        assert after_skip.stdout != first_show

    result = git_stage_batch("undo")
    assert result.returncode == 0
    assert "Undid: skip" in result.stderr
    assert git_stage_batch("show").stdout == first_show


def test_undo_include_line_restores_index(functional_repo):
    """Undoing line include restores the pre-operation index."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    assert "+two" in get_staged_diff("notes.txt")

    git_stage_batch("undo")
    assert get_staged_diff("notes.txt") == ""


def test_undo_discard_line_restores_worktree(functional_repo):
    """Undoing line discard restores the pre-operation working tree bytes."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    original = "one\ntwo\n"
    test_file.write_text(original)

    git_stage_batch("start")
    git_stage_batch("discard", "--line", "1")
    assert test_file.read_text() == "one\n"

    git_stage_batch("undo")
    assert test_file.read_text() == original


def test_undo_refuses_when_worktree_changed_after_checkpoint(functional_repo):
    """Undo refuses to overwrite later worktree edits by default."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    test_file.write_text("one\ntwo\nthree\n")

    result = git_stage_batch("undo", check=False)

    assert result.returncode != 0
    assert "current state has changed since the checkpoint" in result.stderr
    assert "--force" in result.stderr
    assert test_file.read_text() == "one\ntwo\nthree\n"
    assert "+two" in get_staged_diff("notes.txt")


def test_undo_force_overwrites_later_worktree_edits(functional_repo):
    """Undo --force restores checkpoint state even after later edits."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    original = "one\ntwo\n"
    test_file.write_text(original)

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    test_file.write_text("one\ntwo\nthree\n")

    result = git_stage_batch("undo", "--force")

    assert result.returncode == 0
    assert test_file.read_text() == original
    assert get_staged_diff("notes.txt") == ""


def test_undo_include_to_batch_restores_batch_refs(functional_repo):
    """Undoing include-to-batch restores batch content and state refs."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("new", "undo-batch")
    git_stage_batch("start")
    git_stage_batch("include", "--to", "undo-batch", "--line", "1")

    assert git_stage_batch("show", "--from", "undo-batch").stdout
    content_ref = _git("rev-parse", "refs/git-stage-batch/batches/undo-batch").stdout.strip()
    state_ref = _git("rev-parse", "refs/git-stage-batch/state/undo-batch").stdout.strip()

    git_stage_batch("undo")

    assert git_stage_batch("show", "--from", "undo-batch").stdout == ""
    assert _git("rev-parse", "refs/git-stage-batch/batches/undo-batch").stdout.strip() != content_ref
    assert _git("rev-parse", "refs/git-stage-batch/state/undo-batch").stdout.strip() != state_ref

def test_undo_with_empty_stack_fails(repo_with_changes):
    """Undo reports an error when no checkpoint exists."""
    git_stage_batch("start")

    result = git_stage_batch("undo", check=False)

    assert result.returncode != 0
    assert "Nothing to undo" in result.stderr
