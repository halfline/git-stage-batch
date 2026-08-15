"""Tests for undo command."""

import os
import stat
import subprocess

import pytest

import git_stage_batch.data.undo.checkpoints as undo_checkpoints
from git_stage_batch.commands.include import command_include_file, command_include_line
from git_stage_batch.commands.discard import command_discard_file
from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.undo import command_undo
from git_stage_batch.data.undo.checkpoints import (
    redo_last_checkpoint,
    transaction_checkpoint,
    undo_checkpoint,
    undo_last_checkpoint,
)
from git_stage_batch.data.undo.refs import current_undo_commit
from git_stage_batch.data.session import path_is_intent_to_add
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


def _permission_bits(path):
    return stat.S_IMODE(path.lstat().st_mode)


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


@pytest.mark.parametrize(
    ("directory_getter", "expected_label"),
    [
        (get_session_directory_path, "session state"),
        (get_batches_directory_path, "batch metadata"),
    ],
)
def test_undo_refuses_tracked_metadata_drift(
    temp_git_repo,
    directory_getter,
    expected_label,
):
    """Undo should not overwrite metadata changed after checkpoint finalization."""
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    metadata_directory = directory_getter()
    metadata_directory.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_directory / "tracked.txt"
    metadata_path.write_text("before\n")

    with undo_checkpoint("change metadata", worktree_paths=[]):
        metadata_path.write_text("after\n")

    metadata_path.write_text("external drift\n")

    with pytest.raises(CommandError, match=expected_label):
        undo_last_checkpoint()


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


def test_undo_preserves_unrelated_fully_staged_auto_added_file(temp_git_repo):
    """Undo should not demote an unrelated staged new file back to intent-to-add."""
    other = _commit_text_file(temp_git_repo, "other.txt", "other base\n")
    new_file = temp_git_repo / "new.txt"
    new_file.write_text("staged new content\n")
    other.write_text("other changed\n")

    command_start(quiet=True)
    command_include_file("new.txt", quiet=True, advance=False)
    staged_object = subprocess.run(
        ["git", "rev-parse", ":new.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    command_include_file("other.txt", quiet=True, advance=False)

    command_undo(force=True)

    restored_object = subprocess.run(
        ["git", "rev-parse", ":new.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert restored_object == staged_object
    assert _show_index_path(temp_git_repo, "new.txt") == b"staged new content\n"


def test_undo_restores_fully_staged_state_for_scoped_auto_added_file(temp_git_repo):
    """The exact before-image should distinguish staged content from intent-to-add."""
    new_file = temp_git_repo / "new.txt"
    new_file.write_text("staged new content\n")
    command_start(quiet=True)
    command_include_file("new.txt", quiet=True, advance=False)

    with undo_checkpoint("remove staged file", worktree_paths=["new.txt"]):
        subprocess.run(
            ["git", "rm", "--cached", "-f", "--", "new.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

    undo_last_checkpoint(force=True)

    assert _show_index_path(temp_git_repo, "new.txt") == b"staged new content\n"
    assert not path_is_intent_to_add("new.txt")


def test_undo_file_include_restores_both_rename_paths(temp_git_repo):
    """Undoing file-scoped rename staging should restore both index entries."""
    old_path = _commit_text_file(temp_git_repo, "old.txt", "rename content\n")
    new_path = temp_git_repo / "new.txt"
    old_path.rename(new_path)

    command_start(quiet=True)
    command_include_file("new.txt", quiet=True, advance=False)
    command_undo(force=True)

    assert not old_path.exists()
    assert new_path.read_text() == "rename content\n"
    assert _show_index_path(temp_git_repo, "old.txt") == b"rename content\n"
    assert path_is_intent_to_add("new.txt")


def test_undo_file_discard_restores_both_rename_paths(temp_git_repo):
    """Undoing file-scoped rename discard should restore both worktree paths."""
    old_path = _commit_text_file(temp_git_repo, "old.txt", "rename content\n")
    new_path = temp_git_repo / "new.txt"
    old_path.rename(new_path)

    command_start(quiet=True)
    command_discard_file("new.txt", auto_advance=False)
    command_undo(force=True)

    assert not old_path.exists()
    assert new_path.read_text() == "rename content\n"
    assert _show_index_path(temp_git_repo, "old.txt") == b"rename content\n"
    assert path_is_intent_to_add("new.txt")


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


def test_atomic_failed_operation_rolls_back_before_propagating(temp_git_repo):
    """An atomic checkpoint should restore its state and retain no undo node."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    previous_checkpoint = current_undo_commit()

    status = None
    with pytest.raises(RuntimeError, match="operation failed"):
        with undo_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
            rollback_on_error=True,
        ) as status:
            target.write_text("partial mutation\n")
            subprocess.run(
                ["git", "add", "target.txt"],
                check=True,
                cwd=temp_git_repo,
                capture_output=True,
            )
            raise RuntimeError("operation failed")

    assert target.read_text() == "before\n"
    assert _show_index_path(temp_git_repo, "target.txt") == b"before\n"
    assert current_undo_commit() == previous_checkpoint
    assert status is not None
    assert status.rollback == "completed"


def test_active_transaction_rollback_restores_private_metadata_permissions(
    temp_git_repo,
):
    """An unfinalized active checkpoint retains exact metadata modes."""
    marker = get_session_directory_path() / "abort" / "head.txt"
    session_file = get_session_directory_path() / "private-state"
    batch_file = get_batches_directory_path() / "saved" / "private-state"
    marker.parent.mkdir(parents=True, exist_ok=True)
    batch_file.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("HEAD\n")
    session_file.write_text("session before\n")
    batch_file.write_text("batch before\n")
    session_file.chmod(0o600)
    batch_file.chmod(0o600)

    with pytest.raises(RuntimeError, match="publication failed"):
        with transaction_checkpoint(
            "change private metadata",
            worktree_paths=[],
            index_paths=[],
        ) as status:
            status.arm_rollback()
            session_file.write_text("session published\n")
            batch_file.write_text("batch published\n")
            session_file.chmod(0o644)
            batch_file.chmod(0o644)
            raise RuntimeError("publication failed")

    assert session_file.read_text() == "session before\n"
    assert batch_file.read_text() == "batch before\n"
    assert _permission_bits(session_file) == 0o600
    assert _permission_bits(batch_file) == 0o600
    assert status.rollback == "completed"


def test_transaction_status_preserves_unavailable_without_active_session(
    temp_git_repo,
):
    """A missing checkpoint store must not be reported as an unused rollback."""
    with undo_checkpoint(
        "change target",
        worktree_paths=["target.txt"],
        rollback_on_error=True,
    ) as status:
        pass

    assert status.rollback == "unavailable"

    with undo_checkpoint("ordinary change", worktree_paths=[]) as ordinary_status:
        pass

    assert ordinary_status.rollback == "not-requested"


def test_transient_transaction_rolls_back_without_active_session(
    temp_git_repo,
):
    """A required transaction should use a temporary non-session before-image."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    repository_path = (
        temp_git_repo / ".git" / "git-stage-batch" / "transaction-state.txt"
    )
    repository_path.parent.mkdir(parents=True, exist_ok=True)
    repository_path.write_text("repository before\n")
    previous_checkpoint = current_undo_commit()

    status = None
    with pytest.raises(KeyboardInterrupt, match="operation cancelled"):
        with transaction_checkpoint(
            "change targets",
            worktree_paths=["target.txt"],
            repository_paths=[
                "git-stage-batch/transaction-state.txt",
            ],
        ) as status:
            status.arm_rollback()
            target.write_text("worktree partial\n")
            repository_path.write_text("repository partial\n")
            raise KeyboardInterrupt("operation cancelled")

    assert target.read_text() == "before\n"
    assert repository_path.read_text() == "repository before\n"
    assert current_undo_commit() == previous_checkpoint
    assert status is not None
    assert status.rollback == "completed"
    transient_refs = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--format=%(refname)",
            "refs/git-stage-batch/transactions/",
        ],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout
    assert transient_refs == ""


def test_transient_transaction_cleans_ref_after_snapshot_publication_error(
    temp_git_repo,
    monkeypatch,
):
    """A snapshot constructor error must not leak a published temporary ref."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    published_refs = []

    def publish_then_fail(**kwargs):
        ref_name = kwargs["ref_name"]
        published_refs.append(ref_name)
        subprocess.run(
            ["git", "update-ref", ref_name, "HEAD"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        raise RuntimeError("snapshot publication failed")

    monkeypatch.setattr(
        undo_checkpoints._undo_snapshots,
        "write_snapshot_commit",
        publish_then_fail,
    )

    with pytest.raises(RuntimeError, match="snapshot publication failed"):
        with transaction_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
        ):
            target.write_text("must not run\n")

    assert target.read_text() == "before\n"
    assert len(published_refs) == 1
    result = subprocess.run(
        ["git", "rev-parse", "--verify", published_refs[0]],
        check=False,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_transient_snapshot_cleanup_cancellation_preserves_snapshot_error(
    temp_git_repo,
    monkeypatch,
):
    """Best-effort ref cleanup must not replace a snapshot failure."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")

    def fail_snapshot(**_kwargs):
        raise RuntimeError("snapshot publication failed")

    def cancel_cleanup(**_kwargs):
        raise KeyboardInterrupt("cleanup cancelled")

    monkeypatch.setattr(
        undo_checkpoints._undo_snapshots,
        "write_snapshot_commit",
        fail_snapshot,
    )
    monkeypatch.setattr(
        undo_checkpoints,
        "update_git_refs",
        cancel_cleanup,
    )

    with pytest.raises(RuntimeError, match="snapshot publication failed"):
        with transaction_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
        ):
            target.write_text("must not run\n")

    assert target.read_text() == "before\n"


@pytest.mark.parametrize(
    ("arm_rollback", "expected_content", "expected_rollback"),
    [
        (False, "partial\n", "not-needed"),
        (True, "before\n", "completed"),
    ],
)
def test_transient_ref_cleanup_cancellation_preserves_operation_error(
    temp_git_repo,
    monkeypatch,
    arm_rollback,
    expected_content,
    expected_rollback,
):
    """Cleanup cancellation must not mask an armed or unarmed failure."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")

    def cancel_cleanup(_ref_name):
        raise KeyboardInterrupt("cleanup cancelled")

    monkeypatch.setattr(
        undo_checkpoints,
        "_delete_transient_transaction_ref",
        cancel_cleanup,
    )

    status = None
    with pytest.raises(RuntimeError, match="operation failed"):
        with transaction_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
        ) as status:
            if arm_rollback:
                status.arm_rollback()
            target.write_text("partial\n")
            raise RuntimeError("operation failed")

    assert target.read_text() == expected_content
    assert status is not None
    assert status.rollback == expected_rollback


@pytest.mark.parametrize("active_session", (False, True))
def test_unarmed_transaction_refusal_preserves_concurrent_worktree_edit(
    temp_git_repo,
    active_session,
):
    """A refusal before publication must not restore over an external edit."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    if active_session:
        marker = get_session_directory_path() / "abort" / "head.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("HEAD\n")
    previous_checkpoint = current_undo_commit()

    status = None
    with pytest.raises(CommandError, match="stale apply target"):
        with transaction_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
        ) as status:
            target.write_text("concurrent edit\n")
            raise CommandError("stale apply target")

    assert target.read_text() == "concurrent edit\n"
    assert current_undo_commit() == previous_checkpoint
    assert status is not None
    assert status.rollback == "not-needed"
def test_transient_transaction_rolls_back_scoped_index_change(temp_git_repo):
    """A non-session transaction must restore every declared index target."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    neighbor = _commit_text_file(temp_git_repo, "neighbor.txt", "neighbor\n")
    target.write_text("staged\n")
    subprocess.run(
        ["git", "add", "target.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
    )
    expected_index = subprocess.run(
        ["git", "ls-files", "--stage", "--", "target.txt"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True,
    ).stdout

    with pytest.raises(RuntimeError, match="index publication failed"):
        with transaction_checkpoint(
            "change index",
            worktree_paths=[],
            index_paths=["target.txt"],
        ) as status:
            status.arm_rollback()
            subprocess.run(
                ["git", "update-index", "--force-remove", "--", "target.txt"],
                check=True,
                cwd=temp_git_repo,
                capture_output=True,
            )
            neighbor.write_text("concurrent neighbor\n")
            subprocess.run(
                ["git", "add", "neighbor.txt"],
                check=True,
                cwd=temp_git_repo,
                capture_output=True,
            )
            raise RuntimeError("index publication failed")

    assert (
        subprocess.run(
            ["git", "ls-files", "--stage", "--", "target.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout
        == expected_index
    )
    assert (
        subprocess.run(
            ["git", "show", ":neighbor.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        ).stdout
        == "concurrent neighbor\n"
    )


def test_transaction_checkpoint_delegates_to_active_outer_checkpoint(
    temp_git_repo,
):
    """Nested required transactions should share one active-session snapshot."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    marker = get_session_directory_path() / "abort" / "head.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("HEAD\n")

    with transaction_checkpoint(
        "outer change",
        worktree_paths=["target.txt"],
    ) as outer_status:
        outer_status.arm_rollback()
        target.write_text("outer\n")
        with transaction_checkpoint(
            "inner change",
            worktree_paths=["target.txt"],
        ) as inner_status:
            inner_status.arm_rollback()
            target.write_text("inner\n")

    assert outer_status.rollback == "not-needed"
    assert inner_status.rollback == "delegated"
    assert target.read_text() == "inner\n"
    undo_last_checkpoint()
    assert target.read_text() == "before\n"






@pytest.mark.parametrize("active_session", (False, True))
def test_nested_transaction_arm_rolls_back_error_through_outer(
    temp_git_repo,
    active_session,
):
    """An inner publication must arm its enclosing transaction."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    if active_session:
        marker = get_session_directory_path() / "abort" / "head.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("HEAD\n")

    with pytest.raises(RuntimeError, match="inner failed"):
        with transaction_checkpoint(
            "outer change",
            worktree_paths=["target.txt"],
        ) as outer_status:
            with transaction_checkpoint(
                "inner change",
                worktree_paths=["target.txt"],
            ) as inner_status:
                inner_status.arm_rollback()
                target.write_text("inner partial\n")
                raise RuntimeError("inner failed")

    assert outer_status.rollback == "completed"
    assert inner_status.rollback == "delegated"
    assert target.read_text() == "before\n"


@pytest.mark.parametrize("active_session", (False, True))
def test_successful_nested_transaction_arm_rolls_back_later_outer_error(
    temp_git_repo,
    active_session,
):
    """A successful inner publication must remain armed for outer failure."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    if active_session:
        marker = get_session_directory_path() / "abort" / "head.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("HEAD\n")

    with pytest.raises(RuntimeError, match="outer failed"):
        with transaction_checkpoint(
            "outer change",
            worktree_paths=["target.txt"],
        ) as outer_status:
            with transaction_checkpoint(
                "inner change",
                worktree_paths=["target.txt"],
            ) as inner_status:
                inner_status.arm_rollback()
                target.write_text("inner complete\n")
            raise RuntimeError("outer failed")

    assert outer_status.rollback == "completed"
    assert inner_status.rollback == "delegated"
    assert target.read_text() == "before\n"


@pytest.mark.parametrize("active_session", (False, True))
def test_caught_unarmed_nested_refusal_does_not_abort_armed_outer_transaction(
    temp_git_repo,
    active_session,
):
    """A nested pre-publication refusal must not poison an armed outer command."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    if active_session:
        marker = get_session_directory_path() / "abort" / "head.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("HEAD\n")

    with transaction_checkpoint(
        "outer change",
        worktree_paths=["target.txt"],
    ) as outer_status:
        outer_status.arm_rollback()
        target.write_text("outer complete\n")
        with pytest.raises(CommandError, match="stale inner target"):
            with transaction_checkpoint(
                "inner refusal",
                worktree_paths=["target.txt"],
            ) as inner_status:
                raise CommandError("stale inner target")

    assert outer_status.rollback == "not-needed"
    assert inner_status.rollback == "delegated"
    assert target.read_text() == "outer complete\n"
def test_nontransactional_checkpoint_status_remains_not_requested(
    temp_git_repo,
):
    """Successful ordinary and nested checkpoints must not imply rollback."""
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("outer", worktree_paths=[]) as outer_status:
        with undo_checkpoint("inner", worktree_paths=[]) as inner_status:
            pass

    assert inner_status.rollback == "not-requested"
    assert outer_status.rollback == "not-requested"


@pytest.mark.parametrize(
    ("outer_scope", "inner_scope", "scope_name"),
    [
        (
            {"worktree_paths": ["outer.txt"]},
            {"worktree_paths": ["inner.txt"]},
            "worktree",
        ),
        (
            {"worktree_paths": [], "index_paths": ["outer.txt"]},
            {"worktree_paths": [], "index_paths": ["inner.txt"]},
            "index",
        ),
        (
            {"worktree_paths": [], "repository_paths": ["outer"]},
            {"worktree_paths": [], "repository_paths": ["inner"]},
            "repository",
        ),
    ],
)
def test_nested_checkpoint_rejects_paths_outside_outer_scope(
    temp_git_repo,
    outer_scope,
    inner_scope,
    scope_name,
):
    """Nested operations must not mutate paths absent from the before-image."""
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("outer", **outer_scope):
        with pytest.raises(
            CommandError,
            match=rf"does not cover this {scope_name} path.*inner",
        ):
            with undo_checkpoint("inner", **inner_scope):
                raise AssertionError("nested operation should not run")


def test_nested_transaction_requires_transactional_outer_checkpoint(temp_git_repo):
    """A nested rollback promise must not disappear inside a weaker checkpoint."""
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint("outer", worktree_paths=["target.txt"]):
        with pytest.raises(CommandError, match="does not roll back on error"):
            with undo_checkpoint(
                "inner",
                worktree_paths=["target.txt"],
                rollback_on_error=True,
            ):
                raise AssertionError("nested operation should not run")


def test_nested_transaction_uses_compatible_outer_checkpoint(temp_git_repo):
    """A covered nested transaction should share the outer checkpoint."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)

    with undo_checkpoint(
        "outer",
        worktree_paths=["target.txt"],
        rollback_on_error=True,
    ) as outer_status:
        with undo_checkpoint(
            "inner",
            worktree_paths=["target.txt"],
            rollback_on_error=True,
        ) as inner_status:
            target.write_text("after\n")

    assert inner_status.rollback == "delegated"
    assert outer_status.rollback == "not-needed"

    undo_last_checkpoint()

    assert target.read_text() == "before\n"


def test_caught_nested_transaction_error_rolls_back_outer_checkpoint(temp_git_repo):
    """Catching an inner failure must not let its transaction commit."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    previous_checkpoint = current_undo_commit()

    inner_status = None
    with pytest.raises(CommandError, match="enclosing operation was rolled back"):
        with undo_checkpoint(
            "outer",
            worktree_paths=["target.txt"],
            rollback_on_error=True,
        ):
            target.write_text("outer mutation\n")
            try:
                with undo_checkpoint(
                    "inner",
                    worktree_paths=["target.txt"],
                    rollback_on_error=True,
                ) as inner_status:
                    target.write_text("inner mutation\n")
                    raise RuntimeError("inner failed")
            except RuntimeError:
                target.write_text("continued after inner failure\n")

    assert target.read_text() == "before\n"
    assert current_undo_commit() == previous_checkpoint
    assert inner_status is not None
    assert inner_status.rollback == "delegated"


def test_failed_checkpoint_finalization_requires_force(temp_git_repo, monkeypatch):
    """A manifest persistence failure should leave a guarded before-image."""
    from git_stage_batch.data.undo import snapshots as undo_snapshots

    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    original_directory_state = undo_snapshots.filesystem_directory_state
    session_calls = 0

    def fail_during_finalization(*args, **kwargs):
        nonlocal session_calls
        source_dir = args[0] if args else kwargs["source_dir"]
        if source_dir == get_session_directory_path():
            session_calls += 1
            if session_calls > 1:
                raise RuntimeError("manifest persistence failed")
        return original_directory_state(*args, **kwargs)

    monkeypatch.setattr(
        undo_snapshots,
        "filesystem_directory_state",
        fail_during_finalization,
    )

    with pytest.raises(RuntimeError, match="manifest persistence failed"):
        with undo_checkpoint("change target", worktree_paths=["target.txt"]):
            target.write_text("partial mutation\n")

    with pytest.raises(CommandError, match="incomplete checkpoint"):
        undo_last_checkpoint()

    monkeypatch.setattr(
        undo_snapshots,
        "filesystem_directory_state",
        original_directory_state,
    )
    undo_last_checkpoint(force=True)

    assert target.read_text() == "before\n"


def test_unreadable_checkpoint_manifest_fails_finalization(temp_git_repo, monkeypatch):
    """Finalization must report an unreadable before-image manifest."""
    from git_stage_batch.data.undo import checkpoints

    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    get_session_directory_path().mkdir(parents=True, exist_ok=True)
    original_read_json = checkpoints._undo_restore.read_json_from_commit

    def unreadable_manifest(*args, **kwargs):
        raise CommandError("manifest unavailable")

    monkeypatch.setattr(
        checkpoints._undo_restore,
        "read_json_from_commit",
        unreadable_manifest,
    )

    with pytest.raises(CommandError, match="before-image manifest is unavailable"):
        with undo_checkpoint("change target", worktree_paths=["target.txt"]):
            target.write_text("changed\n")

    monkeypatch.setattr(
        checkpoints._undo_restore,
        "read_json_from_commit",
        original_read_json,
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


def test_undo_and_redo_restore_exact_worktree_and_metadata_permissions(
    temp_git_repo,
):
    """Checkpoint manifests retain modes more precise than Git tree modes."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    session_file = get_session_directory_path() / "private-state"
    batch_file = get_batches_directory_path() / "saved" / "private-state"
    repository_relative_path = applied_batch_overlays_repository_path()
    repository_file = temp_git_repo / ".git" / repository_relative_path
    session_file.parent.mkdir(parents=True, exist_ok=True)
    batch_file.parent.mkdir(parents=True, exist_ok=True)
    repository_file.parent.mkdir(parents=True, exist_ok=True)
    for path in (target, session_file, batch_file, repository_file):
        path.write_text("before\n")
        path.chmod(0o600)

    with undo_checkpoint(
        "change private files",
        worktree_paths=["target.txt"],
        repository_paths=[repository_relative_path],
    ):
        for path in (target, session_file, batch_file, repository_file):
            path.write_text("after\n")
            path.chmod(0o640)

    undo_last_checkpoint()

    for path in (target, session_file, batch_file, repository_file):
        assert path.read_text() == "before\n"
        assert _permission_bits(path) == 0o600

    redo_last_checkpoint()

    for path in (target, session_file, batch_file, repository_file):
        assert path.read_text() == "after\n"
        assert _permission_bits(path) == 0o640


def test_transient_rollback_restores_private_overlay_permissions(
    temp_git_repo,
):
    """A failed no-session apply cannot broaden private overlay access."""
    repository_relative_path = applied_batch_overlays_repository_path()
    overlay_path = temp_git_repo / ".git" / repository_relative_path
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_path.write_text("private before\n")
    overlay_path.chmod(0o600)

    with pytest.raises(RuntimeError, match="publication failed"):
        with transaction_checkpoint(
            "change overlay",
            worktree_paths=[],
            index_paths=[],
            repository_paths=[repository_relative_path],
        ) as status:
            status.arm_rollback()
            overlay_path.write_text("published\n")
            overlay_path.chmod(0o644)
            raise RuntimeError("publication failed")

    assert overlay_path.read_text() == "private before\n"
    assert _permission_bits(overlay_path) == 0o600


def test_transient_rollback_restores_exact_worktree_permissions(temp_git_repo):
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    target.chmod(0o600)

    with pytest.raises(RuntimeError, match="publication failed"):
        with transaction_checkpoint(
            "change target",
            worktree_paths=["target.txt"],
            index_paths=[],
        ) as status:
            status.arm_rollback()
            target.write_text("published\n")
            target.chmod(0o644)
            raise RuntimeError("publication failed")

    assert target.read_text() == "before\n"
    assert _permission_bits(target) == 0o600


def test_incomplete_checkpoint_requires_force(temp_git_repo, monkeypatch):
    """A checkpoint interrupted before finalization must not restore silently."""
    from git_stage_batch.data.undo import checkpoints

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


def test_active_transaction_refuses_nested_different_repository(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """An active-session transaction must remain scoped to its repository."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    subprocess.run(
        ["git", "init"],
        check=True,
        cwd=other_repo,
        capture_output=True,
    )
    marker = get_session_directory_path() / "abort" / "head.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("HEAD\n")

    with transaction_checkpoint(
        "outer change",
        worktree_paths=["target.txt"],
    ) as outer_status:
        with monkeypatch.context() as nested_environment:
            nested_environment.chdir(other_repo)
            with pytest.raises(CommandError, match="outer checkpoint.*repository"):
                with transaction_checkpoint(
                    "wrong repository",
                    worktree_paths=[],
                ):
                    raise AssertionError("cross-repository transaction started")
        outer_status.arm_rollback()
        target.write_text("published\n")

    assert outer_status.rollback == "not-needed"
    assert target.read_text() == "published\n"


def test_active_undo_checkpoint_refuses_nested_different_repository(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """A regular pending checkpoint must retain ownership of its repository."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    subprocess.run(
        ["git", "init"],
        check=True,
        cwd=other_repo,
        capture_output=True,
    )
    marker = get_session_directory_path() / "abort" / "head.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("HEAD\n")

    with undo_checkpoint("outer change", worktree_paths=["target.txt"]):
        with monkeypatch.context() as nested_environment:
            nested_environment.chdir(other_repo)
            with pytest.raises(CommandError, match="outer checkpoint.*repository"):
                with undo_checkpoint("wrong repository", worktree_paths=[]):
                    raise AssertionError("cross-repository checkpoint started")
        target.write_text("published\n")

    assert target.read_text() == "published\n"
    undo_last_checkpoint()
    assert target.read_text() == "before\n"


def test_transient_transaction_refuses_nested_different_repository(
    temp_git_repo,
    tmp_path,
    monkeypatch,
):
    """A transient before-image must never delegate work in another repo."""
    target = _commit_text_file(temp_git_repo, "target.txt", "before\n")
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    subprocess.run(
        ["git", "init"],
        check=True,
        cwd=other_repo,
        capture_output=True,
    )

    with transaction_checkpoint(
        "outer change",
        worktree_paths=["target.txt"],
    ) as outer_status:
        with monkeypatch.context() as nested_environment:
            nested_environment.chdir(other_repo)
            with pytest.raises(CommandError, match="outer checkpoint.*repository"):
                with transaction_checkpoint(
                    "wrong repository",
                    worktree_paths=[],
                ):
                    raise AssertionError("cross-repository transaction started")
        outer_status.arm_rollback()
        target.write_text("published\n")

    assert outer_status.rollback == "not-needed"
    assert target.read_text() == "published\n"
