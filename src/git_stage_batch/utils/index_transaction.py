"""Isolated Git index transaction support."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from typing import BinaryIO

from .atomic_write import fsync_directory
from .git_command import run_git_command
from .git_environment import use_git_index_file
from .git_index_lock import (
    DEFAULT_INDEX_LOCK_POLL_SECONDS,
    DEFAULT_INDEX_LOCK_WAIT_SECONDS,
)


_COPY_BUFFER_SIZE = 1024 * 1024


@dataclass(frozen=True)
class _FileSnapshot:
    """A fixed-buffer snapshot of one index-related file."""

    path: Path
    contents: Path | None
    metadata: os.stat_result | None


@dataclass(frozen=True)
class _IndexBaseline:
    """The index state used to detect writes that bypass Git's held lock."""

    index: _FileSnapshot
    shared_index: _FileSnapshot | None


def active_git_index_path(
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return the active index path for the supplied Git environment."""
    git_env = os.environ if env is None else env
    configured_path = git_env.get("GIT_INDEX_FILE")
    if configured_path:
        index_path = Path(configured_path)
        if not index_path.is_absolute():
            index_path = (Path.cwd() if cwd is None else Path(cwd)) / index_path
        return index_path.absolute()

    result = run_git_command(
        ["rev-parse", "--path-format=absolute", "--git-path", "index"],
        cwd=cwd,
        env=None if env is None else dict(env),
        requires_index_lock=False,
    )
    return Path(result.stdout.removesuffix("\n"))


def _index_lock_error(lock_path: Path) -> subprocess.CalledProcessError:
    """Return a CLI-handled Git-style error for a persistent index lock."""
    return subprocess.CalledProcessError(
        128,
        ["git", "update-index"],
        stderr=f"fatal: Unable to create '{lock_path}': File exists.\n",
    )


def _index_changed_error(index_path: Path) -> subprocess.CalledProcessError:
    """Return a CLI-handled error for a failed compare-and-publish step."""
    return subprocess.CalledProcessError(
        1,
        ["git", "update-index"],
        stderr=(
            f"The Git index changed while files were being staged: {index_path}. "
            "No staged changes from this operation were published; retry the "
            "command.\n"
        ),
    )


@contextmanager
def _acquire_index_lock(index_path: Path) -> Iterator[tuple[int, Path]]:
    """Acquire Git's index lock and remove only the lock inode we created."""
    lock_path = Path(f"{index_path}.lock")
    deadline = time.monotonic() + DEFAULT_INDEX_LOCK_WAIT_SECONDS
    file_descriptor: int | None = None
    while file_descriptor is None:
        try:
            file_descriptor = os.open(
                lock_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o666,
            )
        except FileExistsError:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise _index_lock_error(lock_path) from None
            time.sleep(min(DEFAULT_INDEX_LOCK_POLL_SECONDS, remaining_seconds))

    lock_metadata = os.fstat(file_descriptor)
    try:
        yield file_descriptor, lock_path
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass
        try:
            current_metadata = lock_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (
                current_metadata.st_dev == lock_metadata.st_dev
                and current_metadata.st_ino == lock_metadata.st_ino
            ):
                lock_path.unlink(missing_ok=True)


def _copy_file(source: Path, destination: BinaryIO) -> None:
    """Copy one file through a fixed-size userspace buffer."""
    with source.open("rb") as source_file:
        shutil.copyfileobj(source_file, destination, length=_COPY_BUFFER_SIZE)


def _copy_file_to_path(source: Path, destination: Path, *, mode: int) -> None:
    """Copy one regular file to a new path with bounded userspace storage."""
    file_descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        mode,
    )
    try:
        with os.fdopen(file_descriptor, "wb") as destination_file:
            _copy_file(source, destination_file)
            destination_file.flush()
            os.fchmod(destination_file.fileno(), mode)
            os.fsync(destination_file.fileno())
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _snapshot_file(path: Path, destination: Path) -> _FileSnapshot:
    """Copy one regular file into a durable transaction-local snapshot."""
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return _FileSnapshot(path, None, None)
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"Index-related path is not a regular file: {path}")

    _copy_file_to_path(
        path,
        destination,
        mode=stat.S_IMODE(metadata.st_mode),
    )
    return _FileSnapshot(path, destination, metadata)


def _active_shared_index_path(
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> Path | None:
    result = run_git_command(
        ["rev-parse", "--path-format=absolute", "--shared-index-path"],
        cwd=cwd,
        env=None if env is None else dict(env),
        requires_index_lock=False,
    )
    shared_index_name = result.stdout.removesuffix("\n")
    return Path(shared_index_name) if shared_index_name else None


def _snapshot_index(
    index_path: Path,
    transaction_directory: Path,
    transaction_index_path: Path,
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> _IndexBaseline:
    """Prepare an isolated index while the caller holds the real index lock."""
    index_snapshot = _snapshot_file(
        index_path,
        transaction_directory / "baseline-index",
    )
    if index_snapshot.contents is None:
        return _IndexBaseline(index_snapshot, None)

    if index_snapshot.metadata is None:
        raise RuntimeError(
            f"Index snapshot metadata is unavailable: {index_snapshot.path}"
        )
    _copy_file_to_path(
        index_snapshot.contents,
        transaction_index_path,
        mode=stat.S_IMODE(index_snapshot.metadata.st_mode),
    )

    shared_index_path = _active_shared_index_path(cwd=cwd, env=env)
    shared_index_snapshot = (
        _snapshot_file(
            shared_index_path,
            transaction_directory / shared_index_path.name,
        )
        if shared_index_path is not None
        else None
    )
    fsync_directory(transaction_directory)
    return _IndexBaseline(index_snapshot, shared_index_snapshot)


def _files_equal(first: Path, second: Path) -> bool:
    """Compare two files while retaining only two fixed-size chunks."""
    with first.open("rb") as first_file, second.open("rb") as second_file:
        while True:
            first_chunk = first_file.read(_COPY_BUFFER_SIZE)
            second_chunk = second_file.read(_COPY_BUFFER_SIZE)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def _matches_snapshot(snapshot: _FileSnapshot) -> bool:
    """Return whether a path still has the captured bytes and metadata."""
    if snapshot.contents is None:
        return not os.path.lexists(snapshot.path)

    if snapshot.metadata is None:
        raise RuntimeError(f"Snapshot metadata is unavailable: {snapshot.path}")
    try:
        current_metadata = snapshot.path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(current_metadata.st_mode):
        return False
    if (
        current_metadata.st_size != snapshot.metadata.st_size
        or stat.S_IMODE(current_metadata.st_mode)
        != stat.S_IMODE(snapshot.metadata.st_mode)
        or current_metadata.st_uid != snapshot.metadata.st_uid
        or current_metadata.st_gid != snapshot.metadata.st_gid
    ):
        return False
    return _files_equal(snapshot.path, snapshot.contents)


def _write_publication_index(
    transaction_index_path: Path,
    *,
    file_descriptor: int,
    metadata: os.stat_result,
) -> None:
    """Write an isolated index into a durable publication file."""
    with os.fdopen(file_descriptor, "wb", closefd=False) as publication_file:
        _copy_file(transaction_index_path, publication_file)
        publication_file.flush()
        mode = stat.S_IMODE(metadata.st_mode)
        ownership_preserved = True
        if hasattr(os, "fchown"):
            try:
                os.fchown(
                    publication_file.fileno(),
                    metadata.st_uid,
                    metadata.st_gid,
                )
            except PermissionError:
                ownership_preserved = False
        if not ownership_preserved:
            current_metadata = os.fstat(publication_file.fileno())
            group_preserved = current_metadata.st_gid == metadata.st_gid
            if not group_preserved:
                try:
                    os.fchown(
                        publication_file.fileno(),
                        -1,
                        metadata.st_gid,
                    )
                except PermissionError:
                    pass
                else:
                    group_preserved = True
            mode &= 0o770 if group_preserved else 0o700
        os.fchmod(publication_file.fileno(), mode)
        os.fsync(publication_file.fileno())


def _publish_transaction_index(
    baseline: _IndexBaseline,
    transaction_index_path: Path,
    transaction_shared_index: Path | None,
) -> None:
    """Publish an isolated index while the caller retains its real-index lock."""
    index_path = baseline.index.path
    if not _matches_snapshot(baseline.index) or (
        baseline.shared_index is not None
        and not _matches_snapshot(baseline.shared_index)
    ):
        raise _index_changed_error(index_path)

    if not transaction_index_path.exists():
        index_path.unlink(missing_ok=True)
        fsync_directory(index_path.parent)
        return

    transaction_metadata = transaction_index_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(transaction_metadata.st_mode):
        raise RuntimeError(
            f"Transaction index path is not a regular file: {transaction_index_path}"
        )

    if transaction_shared_index is not None:
        shared_metadata = transaction_shared_index.stat(follow_symlinks=False)
        if not stat.S_ISREG(shared_metadata.st_mode):
            raise RuntimeError(
                "Transaction shared-index path is not a regular file: "
                f"{transaction_shared_index}"
            )
    publication_path = transaction_index_path.parent / "published-index"
    publication_descriptor = os.open(
        publication_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        try:
            _write_publication_index(
                transaction_index_path,
                file_descriptor=publication_descriptor,
                metadata=baseline.index.metadata or transaction_metadata,
            )
        finally:
            os.close(publication_descriptor)
        os.replace(publication_path, index_path)
        fsync_directory(index_path.parent)
    finally:
        publication_path.unlink(missing_ok=True)


def _active_worktree_root(
    *,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> Path:
    """Return the worktree boundary used to scope an index override."""
    result = run_git_command(
        ["rev-parse", "--path-format=absolute", "--show-toplevel"],
        cwd=cwd,
        env=None if env is None else dict(env),
        check=False,
        requires_index_lock=False,
    )
    worktree_name = result.stdout.removesuffix("\n")
    if result.returncode == 0 and worktree_name:
        return Path(worktree_name).resolve()
    return (Path.cwd() if cwd is None else Path(cwd)).resolve()


@contextmanager
def isolated_index_transaction(
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Iterator[Callable[[], None]]:
    """Run Git commands on an isolated index and yield its publisher.

    Failed split-index publication can leave an unreferenced, content-addressed
    ``sharedindex.*`` file for Git's normal shared-index expiry to prune.
    """
    index_path = active_git_index_path(cwd=cwd, env=env)
    worktree_root = _active_worktree_root(cwd=cwd, env=env)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with _acquire_index_lock(index_path):
        with tempfile.TemporaryDirectory(
            dir=index_path.parent,
            prefix=".git-stage-batch-index-transaction-",
        ) as transaction_name:
            transaction_directory = Path(transaction_name)
            transaction_index_path = transaction_directory / "index"
            baseline = _snapshot_index(
                index_path,
                transaction_directory,
                transaction_index_path,
                cwd=cwd,
                env=env,
            )
            transaction_environment = None if env is None else dict(env)
            with use_git_index_file(
                transaction_index_path,
                env=transaction_environment,
                config={
                    "core.splitIndex": "false",
                    "splitIndex.sharedIndexExpire": "never",
                },
                worktree_root=worktree_root,
            ):
                if baseline.shared_index is not None:
                    run_git_command(
                        ["update-index", "--no-split-index"],
                        cwd=cwd,
                    )
                published = False

                def publish_index() -> None:
                    nonlocal published
                    if published:
                        return
                    if (
                        baseline.shared_index is not None
                        and transaction_index_path.exists()
                    ):
                        run_git_command(
                            ["update-index", "--split-index"],
                            cwd=cwd,
                        )
                    transaction_shared_index = (
                        _active_shared_index_path(cwd=cwd, env=None)
                        if transaction_index_path.exists()
                        else None
                    )
                    _publish_transaction_index(
                        baseline,
                        transaction_index_path,
                        transaction_shared_index,
                    )
                    published = True

                yield publish_index
                if not published:
                    publish_index()
