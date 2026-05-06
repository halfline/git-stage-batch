"""Functional tests for undo support."""

import json
import subprocess

from .conftest import git_stage_batch, get_staged_diff


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def _commit_file(path, content: str) -> None:
    path.write_text(content)
    _git("add", str(path))
    _git("commit", "-m", f"Add {path.name}")


def _create_two_changed_text_files(repo):
    alpha = repo / "alpha.txt"
    beta = repo / "beta.txt"
    _commit_file(alpha, "alpha\n")
    _commit_file(beta, "beta\n")
    alpha.write_text("alpha\nalpha change\n")
    beta.write_text("beta\nbeta change\n")
    return alpha, beta


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


def test_undo_include_files_restores_entire_multi_file_operation(functional_repo):
    """Undoing include --files should unstage every matched file."""
    alpha, beta = _create_two_changed_text_files(functional_repo)

    git_stage_batch("start")
    git_stage_batch("include", "--files", "*.txt")

    assert set(_git("diff", "--cached", "--name-only").stdout.splitlines()) == {
        "alpha.txt",
        "beta.txt",
    }

    git_stage_batch("undo")

    assert _git("diff", "--cached", "--name-only").stdout == ""
    assert alpha.read_text() == "alpha\nalpha change\n"
    assert beta.read_text() == "beta\nbeta change\n"


def test_undo_skip_files_restores_entire_multi_file_operation(functional_repo):
    """Undoing skip --files should clear every skipped hunk."""
    _create_two_changed_text_files(functional_repo)

    git_stage_batch("start")
    git_stage_batch("skip", "--files", "*.txt")

    skipped_status = json.loads(git_stage_batch("status", "--porcelain").stdout)
    assert skipped_status["progress"]["skipped"] == 2

    git_stage_batch("undo")

    restored_status = json.loads(git_stage_batch("status", "--porcelain").stdout)
    assert restored_status["progress"]["skipped"] == 0


def test_undo_discard_files_restores_entire_multi_file_operation(functional_repo):
    """Undoing discard --files should restore every matched file."""
    alpha, beta = _create_two_changed_text_files(functional_repo)

    git_stage_batch("start")
    git_stage_batch("discard", "--files", "*.txt")

    assert set(_git("diff", "--cached", "--name-only").stdout.splitlines()) == {
        "alpha.txt",
        "beta.txt",
    }

    git_stage_batch("undo")

    assert _git("diff", "--cached", "--name-only").stdout == ""
    assert set(_git("diff", "--name-only").stdout.splitlines()) == {
        "alpha.txt",
        "beta.txt",
    }
    assert alpha.read_text() == "alpha\nalpha change\n"
    assert beta.read_text() == "beta\nbeta change\n"


def test_undo_discard_to_batch_files_restores_entire_multi_file_operation(functional_repo):
    """Undoing discard --to --files should restore every discarded file."""
    alpha, beta = _create_two_changed_text_files(functional_repo)

    git_stage_batch("start")
    git_stage_batch("discard", "--to", "saved", "--files", "*.txt")

    assert _git("diff", "--name-only").stdout == ""

    git_stage_batch("undo")

    assert set(_git("diff", "--name-only").stdout.splitlines()) == {
        "alpha.txt",
        "beta.txt",
    }
    assert alpha.read_text() == "alpha\nalpha change\n"
    assert beta.read_text() == "beta\nbeta change\n"


def test_undo_block_file_restores_gitignore_session_and_index(functional_repo):
    """Undoing block-file during a session restores .gitignore, session state, and index."""
    test_file = functional_repo / "generated.log"
    test_file.write_text("generated\n")

    git_stage_batch("start")
    git_stage_batch("block-file", "generated.log")

    assert not _git("ls-files", "--", "generated.log").stdout.strip()
    assert "generated.log" in (functional_repo / ".gitignore").read_text()

    git_stage_batch("undo")

    assert _git("ls-files", "--", "generated.log").stdout.strip() == "generated.log"
    assert not (functional_repo / ".gitignore").exists()


def test_block_file_outside_session_does_not_create_undo_checkpoint(functional_repo):
    """block-file is undoable only inside an active session."""
    test_file = functional_repo / "generated.log"
    test_file.write_text("generated\n")

    git_stage_batch("block-file", "generated.log")
    result = git_stage_batch("undo", check=False)

    assert result.returncode != 0
    assert "No session in progress" in result.stderr
    assert "generated.log" in (functional_repo / ".gitignore").read_text()


def test_undo_unblock_file_restores_blocked_state(functional_repo):
    """Undoing unblock-file during a session re-blocks the file."""
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


def test_undo_with_empty_stack_fails(repo_with_changes):
    """Undo reports an error when no checkpoint exists."""
    git_stage_batch("start")

    result = git_stage_batch("undo", check=False)

    assert result.returncode != 0
    assert "Nothing to undo" in result.stderr
