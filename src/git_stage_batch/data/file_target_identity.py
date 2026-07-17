"""Stable identities for worktree and index mutation targets."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess

from .index_entries import IndexEntry
from ..exceptions import RepositoryDataInvalid, RepositoryPathInaccessible
from ..utils.git_command import stream_git_command_bytes
from ..utils.git_repository import (
    get_git_repository_root_path,
    is_git_repository_root_path,
)


_DIGEST_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class WorktreeIdentity:
    """Content-bearing identity for one worktree mutation target."""

    exists: bool
    kind: str
    mode: int | None
    size: int | None
    digest: str | None


@dataclass(frozen=True, slots=True)
class IndexIdentity:
    """Stage-zero identity for one index mutation target."""

    mode: str | None
    object_id: str | None

    @property
    def exists(self) -> bool:
        """Return whether the stage-zero index entry exists."""
        return self.object_id is not None

    @property
    def content_object_id(self) -> str | None:
        """Return the object ID when the entry has loadable content."""
        if self.object_id is None or not any(
            character != "0" for character in self.object_id
        ):
            return None
        return self.object_id


def index_identity_from_entry(entry: IndexEntry | None) -> IndexIdentity:
    """Convert an optional stage-zero entry to its compact identity."""
    if entry is None:
        return IndexIdentity(None, None)
    return IndexIdentity(entry.mode, entry.object_id)


def capture_worktree_identity(
    file_path: str,
    *,
    content_artifact_path: str | Path | None = None,
) -> WorktreeIdentity:
    """Capture a target identity and optionally spool its exact text bytes."""
    repository_root = get_git_repository_root_path()
    target_path = repository_root / file_path
    artifact_path = (
        None if content_artifact_path is None else Path(content_artifact_path)
    )
    try:
        metadata = target_path.lstat()
    except FileNotFoundError:
        _write_empty_artifact(artifact_path)
        return WorktreeIdentity(False, "missing", None, None, None)
    except NotADirectoryError:
        _write_empty_artifact(artifact_path)
        return WorktreeIdentity(False, "obstructed", None, None, None)
    except OSError as error:
        raise RepositoryPathInaccessible(
            f"Could not inspect working-tree path {file_path!r}"
        ) from error

    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISREG(metadata.st_mode):
        return _capture_regular_identity(
            file_path,
            target_path,
            metadata,
            artifact_path=artifact_path,
            mode=mode,
        )
    if stat.S_ISLNK(metadata.st_mode):
        try:
            content = os.readlink(os.fsencode(target_path))
        except OSError as error:
            raise RepositoryPathInaccessible(
                f"Could not read working-tree path {file_path!r}"
            ) from error
        _write_artifact_bytes(artifact_path, content)
        return WorktreeIdentity(
            True,
            "symlink",
            mode,
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
    if stat.S_ISDIR(metadata.st_mode):
        if artifact_path is not None:
            raise RepositoryDataInvalid(
                f"Unsupported working-tree path kind: {file_path}"
            )
        return WorktreeIdentity(
            True,
            "directory",
            mode,
            None,
            (
                _submodule_state_digest(target_path)
                if is_git_repository_root_path(target_path)
                else None
            ),
        )
    raise RepositoryDataInvalid(
        f"Unsupported working-tree path kind: {file_path}"
    )


def capture_worktree_identities(
    file_paths: Iterable[str],
) -> dict[str, WorktreeIdentity]:
    """Capture identities in input order for a set of mutation targets."""
    return {
        file_path: capture_worktree_identity(file_path)
        for file_path in dict.fromkeys(file_paths)
    }


def _capture_regular_identity(
    file_path: str,
    target_path: Path,
    expected_metadata: os.stat_result,
    *,
    artifact_path: Path | None,
    mode: int,
) -> WorktreeIdentity:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    output = None
    digest = hashlib.sha256()
    byte_count = 0
    try:
        descriptor = os.open(target_path, flags)
        opened_metadata = os.fstat(descriptor)
        if not _same_opened_file(expected_metadata, opened_metadata):
            raise RepositoryPathInaccessible(
                f"Working-tree path changed while being captured: {file_path!r}"
            )
        if artifact_path is not None:
            output = artifact_path.open("xb")
        while True:
            chunk = os.read(descriptor, _DIGEST_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            if output is not None:
                output.write(chunk)
        final_metadata = os.fstat(descriptor)
        if not _same_opened_file(opened_metadata, final_metadata):
            raise RepositoryPathInaccessible(
                f"Working-tree path changed while being captured: {file_path!r}"
            )
    except OSError as error:
        raise RepositoryPathInaccessible(
            f"Could not read working-tree path {file_path!r}"
        ) from error
    finally:
        try:
            if output is not None:
                output.close()
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return WorktreeIdentity(
        True,
        "regular",
        mode,
        byte_count,
        digest.hexdigest(),
    )


def _same_opened_file(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _submodule_state_digest(path: Path) -> str:
    digest = hashlib.sha256()
    for arguments in (
        ("rev-parse", "--verify", "HEAD^{commit}"),
        ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
    ):
        returncode = 0
        try:
            for chunk in stream_git_command_bytes(
                list(arguments),
                cwd=str(path),
                requires_index_lock=False,
            ):
                digest.update(chunk)
        except subprocess.CalledProcessError as error:
            returncode = error.returncode
        digest.update(b"\0")
        digest.update(str(returncode).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _write_empty_artifact(path: Path | None) -> None:
    if path is None:
        return
    with path.open("xb"):
        pass


def _write_artifact_bytes(path: Path | None, content: bytes) -> None:
    if path is None:
        return
    with path.open("xb") as output:
        output.write(content)
