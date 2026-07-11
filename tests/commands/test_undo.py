"""Tests for undo command."""

import os
import subprocess

import pytest

from git_stage_batch.commands.include import command_include_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.data.undo_checkpoints import (
    redo_last_checkpoint,
    undo_checkpoint,
    undo_last_checkpoint,
)
from git_stage_batch.data.undo_refs import current_undo_commit
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.paths import (
    get_batches_directory_path,
    get_session_directory_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


def _show_index_path(repo, path):
    result = subprocess.run(
        ["git", "show", f":{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return result.stdout


def _commit_symlink(repo, *, target):
    link_path = repo / "link"
    os.symlink(target, link_path)
    subprocess.run(["git", "add", "link"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add link"], check=True, cwd=repo, capture_output=True)
    return link_path


def _commit_text_file(repo, path: str, content: str):
    file_path = repo / path
    file_path.write_text(content)
    subprocess.run(["git", "add", path], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Add {path}"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return file_path


def test_undo_include_line_restores_symlink_worktree_snapshot(temp_git_repo):
    """Undo should restore a symlink target, not the referent bytes."""
    link_path = _commit_symlink(temp_git_repo, target="old")
    link_path.unlink()
    os.symlink("new", link_path)
    (temp_git_repo / "new").write_bytes(b"referent\n")

    command_start(quiet=True)
    command_include_line("1,2")

    command_undo(force=True)

    assert os.path.islink(link_path)
    assert os.readlink(link_path) == "new"
    assert _show_index_path(temp_git_repo, "link") == b"old"


def test_undo_include_line_restores_dangling_symlink_snapshot(temp_git_repo):
    """Undo should restore dangling symlinks as existing worktree paths."""
    link_path = _commit_symlink(temp_git_repo, target="old")
    link_path.unlink()
    os.symlink("missing", link_path)

    command_start(quiet=True)
    command_include_line("1,2")

    command_undo(force=True)

    assert os.path.islink(link_path)
    assert os.readlink(link_path) == "missing"
    assert _show_index_path(temp_git_repo, "link") == b"old"


def test_scoped_undo_ignores_unrelated_untracked_worktree_edits(temp_git_repo):
    """Explicit checkpoint scopes should not conflict on unrelated dirty files."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    unrelated = temp_git_repo / "unrelated.txt"
    unrelated.write_text("first\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("change target", worktree_paths=["target.txt"]):
        target.write_text("during\n")

    unrelated.write_text("second\n")
    undo_last_checkpoint()

    assert target.read_text() == "before\n"
    assert unrelated.read_text() == "second\n"


def test_scoped_checkpoint_does_not_retain_unrelated_content(temp_git_repo):
    """A narrow checkpoint tree should contain only its declared worktree path."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    unrelated = temp_git_repo / "unrelated-secret.txt"
    unrelated.write_text("content that must not be retained\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("change target", worktree_paths=["target.txt"]):
        target.write_text("after\n")

    checkpoint = current_undo_commit()
    assert checkpoint is not None
    tree_paths = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", checkpoint],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert "worktree/target.txt" in tree_paths
    assert "worktree/unrelated-secret.txt" not in tree_paths


def test_scoped_undo_preserves_unrelated_index_changes(temp_git_repo):
    """Undo should restore scoped index entries without replacing the whole index."""
    target = _commit_text_file(temp_git_repo, "target.txt", "target base\n")
    unrelated = _commit_text_file(
        temp_git_repo,
        "unrelated.txt",
        "unrelated base\n",
    )
    target.write_text("target staged\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("stage target", worktree_paths=["target.txt"]):
        subprocess.run(
            ["git", "add", "target.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

    unrelated.write_text("unrelated staged later\n")
    subprocess.run(
        ["git", "add", "unrelated.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )

    undo_last_checkpoint()

    staged_paths = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged_paths == ["unrelated.txt"]
    assert target.read_text() == "target staged\n"


def test_failed_operation_keeps_partial_mutation_undoable(temp_git_repo):
    """An operation error should finalize its checkpoint before propagating."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="operation failed"):
        with undo_checkpoint("change target", worktree_paths=["target.txt"]):
            target.write_text("partial mutation\n")
            raise RuntimeError("operation failed")

    undo_last_checkpoint()

    assert target.read_text() == "before\n"


def test_failed_checkpoint_finalization_requires_force(temp_git_repo, monkeypatch):
    """A manifest persistence failure should leave a guarded before-image."""
    from git_stage_batch.data import undo_checkpoints as checkpoints

    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    original_directory_state = checkpoints._filesystem_directory_state
    calls = 0

    def fail_during_finalization(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise RuntimeError("manifest persistence failed")
        return original_directory_state(*args, **kwargs)

    monkeypatch.setattr(
        checkpoints,
        "_filesystem_directory_state",
        fail_during_finalization,
    )

    with pytest.raises(RuntimeError, match="manifest persistence failed"):
        with undo_checkpoint("change target", worktree_paths=["target.txt"]):
            target.write_text("partial mutation\n")

    with pytest.raises(CommandError, match="incomplete checkpoint"):
        undo_last_checkpoint()

    monkeypatch.setattr(
        checkpoints,
        "_filesystem_directory_state",
        original_directory_state,
    )
    undo_last_checkpoint(force=True)

    assert target.read_text() == "before\n"


def test_scoped_undo_preserves_unrelated_batch_ref_changes(temp_git_repo):
    """Undo should restore changed batch refs without replacing unrelated refs."""
    target_ref = "refs/git-stage-batch/batches/target"
    unrelated_ref = "refs/git-stage-batch/batches/unrelated"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def create_commit(message):
        return subprocess.run(
            ["git", "commit-tree", tree, "-m", message],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

    target_after = create_commit("target after")
    unrelated_after = create_commit("unrelated after")
    subprocess.run(
        ["git", "update-ref", target_ref, head],
        check=True,
        cwd=temp_git_repo,
    )
    subprocess.run(
        ["git", "update-ref", unrelated_ref, head],
        check=True,
        cwd=temp_git_repo,
    )
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("move target ref", worktree_paths=[]):
        subprocess.run(
            ["git", "update-ref", target_ref, target_after],
            check=True,
            cwd=temp_git_repo,
        )

    subprocess.run(
        ["git", "update-ref", unrelated_ref, unrelated_after],
        check=True,
        cwd=temp_git_repo,
    )

    undo_last_checkpoint()

    assert subprocess.run(
        ["git", "rev-parse", target_ref],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip() == head
    assert subprocess.run(
        ["git", "rev-parse", unrelated_ref],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip() == unrelated_after


def test_scoped_undo_preserves_unrelated_application_metadata(temp_git_repo):
    """Final checkpoints should retain and restore only changed state files."""
    session_dir = get_session_directory_path()
    batches_dir = get_batches_directory_path()
    session_dir.mkdir(parents=True, exist_ok=True)
    batches_dir.mkdir(parents=True, exist_ok=True)
    target_session = session_dir / "target-state"
    unrelated_session = session_dir / "unrelated-state"
    target_batch = batches_dir / "target" / "metadata.json"
    unrelated_batch = batches_dir / "unrelated" / "metadata.json"
    target_batch.parent.mkdir()
    unrelated_batch.parent.mkdir()
    target_session.write_text("before\n")
    unrelated_session.write_text("unrelated before\n")
    target_batch.write_text("before\n")
    unrelated_batch.write_text("unrelated before\n")

    with undo_checkpoint("change metadata", worktree_paths=[]):
        target_session.write_text("after\n")
        target_batch.write_text("after\n")

    unrelated_session.write_text("unrelated later\n")
    unrelated_batch.write_text("unrelated later\n")
    checkpoint = current_undo_commit()
    assert checkpoint is not None
    tree_paths = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", checkpoint],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert "session/target-state" in tree_paths
    assert "batches/target/metadata.json" in tree_paths
    assert "session/unrelated-state" not in tree_paths
    assert "batches/unrelated/metadata.json" not in tree_paths

    undo_last_checkpoint()

    assert target_session.read_text() == "before\n"
    assert target_batch.read_text() == "before\n"
    assert unrelated_session.read_text() == "unrelated later\n"
    assert unrelated_batch.read_text() == "unrelated later\n"

    redo_last_checkpoint()

    assert target_session.read_text() == "after\n"
    assert target_batch.read_text() == "after\n"
    assert unrelated_session.read_text() == "unrelated later\n"
    assert unrelated_batch.read_text() == "unrelated later\n"


def test_incomplete_checkpoint_requires_force(temp_git_repo, monkeypatch):
    """A checkpoint interrupted before finalization must not restore silently."""
    from git_stage_batch.data import undo_checkpoints as checkpoints

    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    checkpoints._create_undo_checkpoint(
        "interrupted change",
        worktree_paths=["target.txt"],
    )
    monkeypatch.setattr(checkpoints, "_PENDING_CHECKPOINT", None)
    target.write_text("after process exit\n")

    with pytest.raises(CommandError, match="incomplete checkpoint"):
        undo_last_checkpoint()

    assert target.read_text() == "after process exit\n"
