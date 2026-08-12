"""Tests for isolated Git index transactions."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess

import pytest

from git_stage_batch.utils import index_transaction
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.index_transaction import (
    _acquire_index_lock,
    active_git_index_path,
    isolated_index_transaction,
)


class ExpectedFailure(RuntimeError):
    """Sentinel error used to exercise transaction cleanup."""


def _git(
    repo: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


def _transaction_git(
    repo: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[bytes]:
    result = run_git_command(
        list(arguments),
        cwd=str(repo),
        text_output=False,
    )
    assert isinstance(result.stdout, bytes)
    return result


def _initialize_repository(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    for path in ("target.txt", "concurrent.txt", "assume.txt", "skip.txt"):
        (repo / path).write_text(f"{path}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")


def _debug_index(repo: Path, *, env: dict[str, str] | None = None) -> bytes:
    return _git(repo, "ls-files", "--stage", "--debug", "-z", env=env).stdout


def _cached_paths(repo: Path) -> set[bytes]:
    output = _git(repo, "diff", "--cached", "--name-only", "-z").stdout
    return {path for path in output.split(b"\0") if path}


def test_failed_transaction_discards_exact_index_flags_and_split_state(tmp_path):
    """A failed transaction must leave the complete original index untouched."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "intent.txt").write_text("intent\n")
    _git(repo, "add", "-N", "intent.txt")
    _git(repo, "update-index", "--assume-unchanged", "assume.txt")
    _git(repo, "update-index", "--skip-worktree", "skip.txt")
    _git(repo, "update-index", "--split-index")
    _git(repo, "config", "splitIndex.sharedIndexExpire", "now")

    index_path = active_git_index_path(cwd=str(repo))
    shared_index_path = Path(
        _git(
            repo,
            "rev-parse",
            "--path-format=absolute",
            "--shared-index-path",
        ).stdout.rstrip(b"\n").decode()
    )
    index_path.chmod(0o640)
    before_bytes = index_path.read_bytes()
    before_debug = _debug_index(repo)

    with pytest.raises(ExpectedFailure):
        with isolated_index_transaction(cwd=str(repo)):
            (repo / "target.txt").write_text("changed\n")
            _transaction_git(repo, "add", "target.txt")
            assert shared_index_path.exists()
            raise ExpectedFailure

    assert index_path.read_bytes() == before_bytes
    assert _debug_index(repo) == before_debug
    assert stat.S_IMODE(index_path.stat().st_mode) == 0o640
    assert shared_index_path.exists()


def test_successful_transaction_publishes_flags_split_state_and_mode(tmp_path):
    """A successful transaction must publish through the original index mode."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "intent.txt").write_text("intent\n")
    _git(repo, "add", "-N", "intent.txt")
    _git(repo, "update-index", "--assume-unchanged", "assume.txt")
    _git(repo, "update-index", "--skip-worktree", "skip.txt")
    _git(repo, "update-index", "--split-index")

    index_path = active_git_index_path(cwd=str(repo))
    index_path.chmod(0o640)
    (repo / "target.txt").write_text("changed\n")

    with isolated_index_transaction(cwd=str(repo)):
        _transaction_git(repo, "add", "target.txt")

    debug_state = _debug_index(repo)
    shared_index_name = _git(
        repo,
        "rev-parse",
        "--path-format=absolute",
        "--shared-index-path",
    ).stdout.rstrip(b"\n")
    assert _cached_paths(repo) == {b"target.txt"}
    assert b"intent.txt" in debug_state
    assert b"assume.txt" in debug_state
    assert b"skip.txt" in debug_state
    assert stat.S_IMODE(index_path.stat().st_mode) == 0o640
    assert shared_index_name
    assert Path(shared_index_name.decode()).exists()


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="requires POSIX fchown")
def test_transaction_preserves_shared_group_mode_when_owner_restore_fails(
    tmp_path,
    monkeypatch,
):
    """A failed owner restore must not strip an already-correct group mode."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    index_path = active_git_index_path(cwd=str(repo))
    index_path.chmod(0o660)
    (repo / "target.txt").write_text("changed\n")

    def reject_owner_restore(
        _file_descriptor: int,
        _user_id: int,
        _group_id: int,
    ) -> None:
        raise PermissionError

    monkeypatch.setattr(index_transaction.os, "fchown", reject_owner_restore)

    with isolated_index_transaction(cwd=str(repo)):
        _transaction_git(repo, "add", "target.txt")

    assert stat.S_IMODE(index_path.stat().st_mode) == 0o660


def test_transaction_does_not_redirect_nested_repository_commands(tmp_path):
    """A parent transaction must leave a nested repository's index alone."""
    repo = tmp_path / "repo"
    nested_repo = repo / "nested"
    _initialize_repository(repo)
    _initialize_repository(nested_repo)
    (repo / "target.txt").write_text("parent change\n")
    (nested_repo / "target.txt").write_text("nested change\n")

    with isolated_index_transaction(cwd=str(repo)):
        _transaction_git(repo, "add", "target.txt")
        _transaction_git(nested_repo, "add", "target.txt")

    assert _cached_paths(repo) == {b"target.txt"}
    assert _cached_paths(nested_repo) == {b"target.txt"}


def test_partial_explicit_environment_stays_in_transaction(tmp_path):
    """An explicit environment without its own index must stay isolated."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "target.txt").write_text("changed\n")
    partial_environment = {"PATH": os.environ["PATH"]}

    with pytest.raises(ExpectedFailure):
        with isolated_index_transaction(cwd=str(repo)):
            run_git_command(
                ["add", "target.txt"],
                cwd=str(repo),
                env=partial_environment,
                requires_index_lock=False,
            )
            raise ExpectedFailure

    assert _cached_paths(repo) == set()


def test_explicit_alternate_index_environment_opts_out(tmp_path):
    """An explicit alternate index must remain independent of a transaction."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    alternate_environment = os.environ.copy()
    alternate_environment["GIT_INDEX_FILE"] = str(repo / "alternate-index")
    _git(repo, "read-tree", "HEAD", env=alternate_environment)
    (repo / "target.txt").write_text("transaction change\n")
    (repo / "concurrent.txt").write_text("alternate change\n")

    with isolated_index_transaction(cwd=str(repo)):
        _transaction_git(repo, "add", "target.txt")
        run_git_command(
            ["add", "concurrent.txt"],
            cwd=str(repo),
            env=alternate_environment,
            requires_index_lock=False,
        )

    alternate_paths = _git(
        repo,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        env=alternate_environment,
    ).stdout
    assert _cached_paths(repo) == {b"target.txt"}
    assert {path for path in alternate_paths.split(b"\0") if path} == {
        b"concurrent.txt"
    }


def test_transaction_honors_relative_git_index_file(tmp_path):
    """A relative GIT_INDEX_FILE must be isolated instead of the main index."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = "alternate-index"
    _git(repo, "read-tree", "HEAD", env=environment)
    alternate_path = repo / "alternate-index"
    before_main = active_git_index_path(cwd=str(repo)).read_bytes()

    with isolated_index_transaction(cwd=str(repo), env=environment):
        _transaction_git(repo, "read-tree", "--empty")

    assert _debug_index(repo, env=environment) == b""
    assert alternate_path.exists()
    assert active_git_index_path(cwd=str(repo)).read_bytes() == before_main


def test_failed_transaction_leaves_initially_absent_index_absent(tmp_path):
    """A failed transaction must not create an initially absent index path."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    environment = os.environ.copy()
    environment["GIT_INDEX_FILE"] = str(repo / "new-index")

    with pytest.raises(ExpectedFailure):
        with isolated_index_transaction(cwd=str(repo), env=environment):
            _transaction_git(repo, "read-tree", "HEAD")
            raise ExpectedFailure

    assert not (repo / "new-index").exists()


def test_transaction_uses_linked_worktree_index(tmp_path):
    """A linked-worktree transaction must leave the main index untouched."""
    repo = tmp_path / "repo"
    linked = tmp_path / "linked"
    _initialize_repository(repo)
    _git(repo, "worktree", "add", "-qb", "linked", str(linked))

    main_index = active_git_index_path(cwd=str(repo))
    linked_index = active_git_index_path(cwd=str(linked))
    before_main = main_index.read_bytes()
    (linked / "target.txt").write_text("linked change\n")

    assert linked_index != main_index
    with isolated_index_transaction(cwd=str(linked)):
        _transaction_git(linked, "add", "target.txt")

    assert _cached_paths(linked) == {b"target.txt"}
    assert main_index.read_bytes() == before_main


def test_failed_transaction_blocks_concurrent_real_index_update(tmp_path):
    """Failure must retain the real-index lock while private writes run."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "target.txt").write_text("transaction\n")
    (repo / "concurrent.txt").write_text("external\n")

    with pytest.raises(ExpectedFailure):
        with isolated_index_transaction(cwd=str(repo)):
            _transaction_git(repo, "add", "target.txt")
            with pytest.raises(subprocess.CalledProcessError) as error:
                _git(repo, "add", "concurrent.txt")
            assert b"index.lock" in error.value.stderr
            raise ExpectedFailure

    assert _cached_paths(repo) == set()


def test_successful_transaction_blocks_concurrent_real_index_update(tmp_path):
    """Success must publish after retaining the real-index lock throughout."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "target.txt").write_text("transaction\n")
    (repo / "concurrent.txt").write_text("external\n")

    with isolated_index_transaction(cwd=str(repo)):
        _transaction_git(repo, "add", "target.txt")
        with pytest.raises(subprocess.CalledProcessError) as error:
            _git(repo, "add", "concurrent.txt")

    assert b"index.lock" in error.value.stderr
    assert _cached_paths(repo) == {b"target.txt"}


def test_explicit_publication_retains_real_index_lock(tmp_path):
    """An early publication must not release the transaction's exclusion."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "target.txt").write_text("transaction\n")
    (repo / "concurrent.txt").write_text("external\n")

    with isolated_index_transaction(cwd=str(repo)) as publish_index:
        _transaction_git(repo, "add", "target.txt")
        publish_index()

        assert _cached_paths(repo) == {b"target.txt"}
        with pytest.raises(subprocess.CalledProcessError) as error:
            _git(repo, "add", "concurrent.txt")

    assert b"index.lock" in error.value.stderr
    assert _cached_paths(repo) == {b"target.txt"}


def test_transaction_refuses_index_replacement_that_bypasses_lock(tmp_path):
    """Publication must fail closed when a writer ignores Git's index lock."""
    repo = tmp_path / "repo"
    _initialize_repository(repo)
    (repo / "target.txt").write_text("transaction\n")
    (repo / "concurrent.txt").write_text("external\n")
    index_path = active_git_index_path(cwd=str(repo))
    external_index_path = repo / "external-index"
    external_environment = os.environ.copy()
    external_environment["GIT_INDEX_FILE"] = str(external_index_path)
    _git(repo, "read-tree", "HEAD", env=external_environment)
    _git(repo, "add", "concurrent.txt", env=external_environment)

    with pytest.raises(subprocess.CalledProcessError) as error:
        with isolated_index_transaction(cwd=str(repo)):
            _transaction_git(repo, "add", "target.txt")
            os.replace(external_index_path, index_path)

    assert "Git index changed" in error.value.stderr
    assert _cached_paths(repo) == {b"concurrent.txt"}


def test_index_lock_cleanup_does_not_remove_successor_lock(tmp_path):
    """Publishing the acquired lock must not make its successor look owned."""
    index_path = tmp_path / "index"
    lock_path = Path(f"{index_path}.lock")

    with _acquire_index_lock(index_path) as (_file_descriptor, acquired_path):
        os.replace(acquired_path, index_path)
        successor_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(successor_descriptor)

    assert lock_path.exists()


def test_index_lock_timeout_raises_cli_handled_git_error(tmp_path, monkeypatch):
    """A persistent index lock wait must not escape as a traceback error."""
    index_path = tmp_path / "index"
    lock_path = Path(f"{index_path}.lock")
    lock_path.write_bytes(b"successor")
    monkeypatch.setattr(index_transaction, "DEFAULT_INDEX_LOCK_WAIT_SECONDS", 0.0)

    with pytest.raises(subprocess.CalledProcessError) as error:
        with _acquire_index_lock(index_path):
            raise AssertionError("the persistent lock must prevent entry")

    assert error.value.returncode == 128
    assert "Unable to create" in error.value.stderr
    assert lock_path.read_bytes() == b"successor"
