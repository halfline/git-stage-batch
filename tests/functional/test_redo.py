"""Functional tests for redo support."""

import subprocess

from .conftest import git_stage_batch, get_staged_diff


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _commit_file(path, content: str) -> None:
    path.write_text(content)
    _git("add", str(path))
    _git("commit", "-m", f"Add {path.name}")


def test_redo_after_undo_include_line_restores_index(functional_repo):
    """Redoing an undone include --line restores the staged diff."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    assert "+two" in get_staged_diff("notes.txt")

    git_stage_batch("undo")
    assert get_staged_diff("notes.txt") == ""

    result = git_stage_batch("redo")
    assert result.returncode == 0
    assert "Redid: include --line 1" in result.stderr
    assert "+two" in get_staged_diff("notes.txt")


def test_redo_after_undo_discard_line_restores_worktree(functional_repo):
    """Redoing an undone discard --line re-discards the line."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    original = "one\ntwo\n"
    test_file.write_text(original)

    git_stage_batch("start")
    git_stage_batch("discard", "--line", "1")
    assert test_file.read_text() == "one\n"

    git_stage_batch("undo")
    assert test_file.read_text() == original

    git_stage_batch("redo")
    assert test_file.read_text() == "one\n"


def test_multiple_undo_redo_order(functional_repo):
    """Multiple undo/redo cycles work in editor order."""
    file_a = functional_repo / "a.txt"
    file_b = functional_repo / "b.txt"
    _commit_file(file_a, "a-base\n")
    _commit_file(file_b, "b-base\n")
    file_a.write_text("a-base\na-new\n")
    file_b.write_text("b-base\nb-new\n")

    git_stage_batch("start")

    git_stage_batch("include", "--line", "1")

    git_stage_batch("include", "--line", "1")

    git_stage_batch("undo")
    git_stage_batch("undo")

    staged = get_staged_diff()
    assert "+a-new" not in staged
    assert "+b-new" not in staged

    result = git_stage_batch("redo")
    assert result.returncode == 0

    result = git_stage_batch("redo")
    assert result.returncode == 0

    staged = get_staged_diff()
    assert "+a-new" in staged
    assert "+b-new" in staged


def test_new_operation_after_undo_clears_redo(functional_repo):
    """A new undoable operation after undo clears the redo stack."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\nthree\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    git_stage_batch("undo")

    git_stage_batch("skip")

    result = git_stage_batch("redo", check=False)
    assert result.returncode != 0
    assert "Nothing to redo" in result.stderr


def test_redo_refuses_when_state_changed_after_undo(functional_repo):
    """Redo refuses if the user modified state after the undo."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    git_stage_batch("undo")

    test_file.write_text("one\ntwo\nthree\n")

    result = git_stage_batch("redo", check=False)
    assert result.returncode != 0
    assert "current state has changed since the undo" in result.stderr
    assert "--force" in result.stderr


def test_redo_force_overwrites_later_changes(functional_repo):
    """Redo --force overwrites changes made after the undo."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("start")
    git_stage_batch("include", "--line", "1")
    git_stage_batch("undo")

    test_file.write_text("one\ntwo\nthree\n")

    result = git_stage_batch("redo", "--force")
    assert result.returncode == 0
    assert "+two" in get_staged_diff("notes.txt")


def test_redo_with_empty_stack_fails(repo_with_changes):
    """Redo reports an error when no redo node exists."""
    git_stage_batch("start")

    result = git_stage_batch("redo", check=False)

    assert result.returncode != 0
    assert "Nothing to redo" in result.stderr


def test_redo_include_to_batch_restores_batch_refs(functional_repo):
    """Redoing an undone include-to-batch restores batch content and state refs."""
    test_file = functional_repo / "notes.txt"
    _commit_file(test_file, "one\n")
    test_file.write_text("one\ntwo\n")

    git_stage_batch("new", "redo-batch")
    git_stage_batch("start")
    git_stage_batch("include", "--to", "redo-batch", "--line", "1")

    batch_content = git_stage_batch("show", "--from", "redo-batch").stdout
    assert batch_content
    content_ref = _git("rev-parse", "refs/git-stage-batch/batches/redo-batch").stdout.strip()
    state_ref = _git("rev-parse", "refs/git-stage-batch/state/redo-batch").stdout.strip()

    git_stage_batch("undo")
    assert git_stage_batch("show", "--from", "redo-batch").stdout == ""

    git_stage_batch("redo")
    assert git_stage_batch("show", "--from", "redo-batch").stdout == batch_content
    assert _git("rev-parse", "refs/git-stage-batch/batches/redo-batch").stdout.strip() == content_ref
    assert _git("rev-parse", "refs/git-stage-batch/state/redo-batch").stdout.strip() == state_ref


def test_redo_block_file_restores_blocked_state(functional_repo):
    """Redoing an undone block-file re-blocks the file."""
    test_file = functional_repo / "generated.log"
    test_file.write_text("generated\n")

    git_stage_batch("start")
    git_stage_batch("block-file", "generated.log")

    assert not _git("ls-files", "--", "generated.log").stdout.strip()
    assert "generated.log" in (functional_repo / ".gitignore").read_text()

    git_stage_batch("undo")

    assert _git("ls-files", "--", "generated.log").stdout.strip() == "generated.log"
    assert not (functional_repo / ".gitignore").exists()

    git_stage_batch("redo")

    assert not _git("ls-files", "--", "generated.log").stdout.strip()
    assert "generated.log" in (functional_repo / ".gitignore").read_text()


def test_redo_unblock_file_restores_unblocked_state(functional_repo):
    """Redoing an undone unblock-file re-unblocks the file."""
    test_file = functional_repo / "generated.log"
    test_file.write_text("generated\n")

    git_stage_batch("start")
    git_stage_batch("block-file", "generated.log")
    git_stage_batch("unblock-file", "generated.log")

    assert _git("ls-files", "--", "generated.log").stdout.strip() == "generated.log"
    assert "generated.log" not in (functional_repo / ".gitignore").read_text()

    git_stage_batch("undo")

    assert not _git("ls-files", "--", "generated.log").stdout.strip()
    assert "generated.log" in (functional_repo / ".gitignore").read_text()

    git_stage_batch("redo")

    assert _git("ls-files", "--", "generated.log").stdout.strip() == "generated.log"
    assert "generated.log" not in (functional_repo / ".gitignore").read_text()


def test_redo_forward_alias_works(repo_with_changes):
    """The 'forward' alias works for redo."""
    git_stage_batch("start")

    result = git_stage_batch("forward", check=False)
    assert result.returncode != 0
    assert "Nothing to redo" in result.stderr
