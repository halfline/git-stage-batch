"""Git object IO helpers."""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from .git_environment import pin_git_object_environment
from ..git_paths import decode_path, nul_records


_EMPTY_TREE_OBJECT_CACHE: dict[Path, str] = {}
_QUARANTINE_CONSTRUCTION_TOKEN = object()
_OBJECT_ENVIRONMENT_KEYS = (
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_QUARANTINE_PATH",
    "GIT_STAGE_BATCH_PINNED_OBJECT_ENVIRONMENT",
)


def _open_lendable_directory_capability(path: Path, flags: int) -> int:
    descriptor = os.open(path, flags)
    if descriptor >= 3:
        return descriptor
    duplicate_command = getattr(fcntl, "F_DUPFD_CLOEXEC", None)
    if duplicate_command is None:
        os.close(descriptor)
        raise RuntimeError("atomic Git object directory lending is unsupported")
    try:
        duplicate = fcntl.fcntl(descriptor, duplicate_command, 3)
    finally:
        os.close(descriptor)
    return duplicate


@dataclass(frozen=True, slots=True)
class _GitRepositoryObjectIdentity:
    object_format: str
    common_directory: Path
    common_device: int
    common_inode: int
    object_directory: Path
    object_device: int
    object_inode: int

    def matches(self, other: _GitRepositoryObjectIdentity) -> bool:
        """Return whether every certified repository identity fact agrees."""
        return (
            self.object_format == other.object_format
            and self.common_directory == other.common_directory
            and self.common_device == other.common_device
            and self.common_inode == other.common_inode
            and self.object_directory == other.object_directory
            and self.object_device == other.object_device
            and self.object_inode == other.object_inode
        )


@dataclass(frozen=True, slots=True, init=False)
class GitObjectQuarantine:
    """Product-issued environment capability for temporary Git objects."""

    _environment: Mapping[str, str]
    _persistent_environment: Mapping[str, str]
    _persistent_identity: _GitRepositoryObjectIdentity
    _object_directory: Path
    _object_device: int
    _object_inode: int

    def __init__(
        self,
        environment: dict[str, str],
        persistent_environment: dict[str, str],
        persistent_identity: _GitRepositoryObjectIdentity,
        object_directory: Path,
        object_device: int,
        object_inode: int,
        *,
        _token: object,
    ) -> None:
        if _token is not _QUARANTINE_CONSTRUCTION_TOKEN:
            raise ValueError("Git object quarantines must be product-issued")
        object.__setattr__(
            self,
            "_environment",
            MappingProxyType(dict(environment)),
        )
        object.__setattr__(
            self,
            "_persistent_environment",
            MappingProxyType(dict(persistent_environment)),
        )
        object.__setattr__(self, "_persistent_identity", persistent_identity)
        object.__setattr__(self, "_object_directory", object_directory)
        object.__setattr__(self, "_object_device", object_device)
        object.__setattr__(self, "_object_inode", object_inode)

    def environment(self) -> dict[str, str]:
        """Return one disposable copy of the certified quarantine environment."""
        return dict(self._environment)

    @property
    def object_format(self) -> str:
        """Return the certified persistent repository object format."""
        return self._persistent_identity.object_format

    def persistent_environment(self) -> dict[str, str]:
        """Return a disposable environment for the persistent object store."""
        return dict(self._persistent_environment)

    def require_persistent_identity(self) -> None:
        """Require the certified persistent repository object store identity."""
        current = _git_repository_object_identity(self.persistent_environment())
        if not self._persistent_identity.matches(current):
            raise RuntimeError("The persistent Git object store identity changed")

    def require_quarantine_object_directory_identity(
        self,
        descriptor: int,
    ) -> tuple[int, int]:
        """Require one pin and the visible quarantine directory to stay certified."""
        try:
            opened = os.fstat(descriptor)
            visible = os.stat(self._object_directory, follow_symlinks=False)
        except OSError as error:
            raise RuntimeError(
                f"Cannot authenticate the Git object quarantine: {error}"
            ) from error
        expected = self._object_device, self._object_inode
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (opened.st_dev, opened.st_ino) != expected
            or (visible.st_dev, visible.st_ino) != expected
        ):
            raise RuntimeError("The Git object quarantine identity changed")
        return expected

    @contextmanager
    def pinned_quarantine_object_directory(self) -> Iterator[int]:
        """Yield a descriptor pinned to the certified quarantine object store."""
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = _open_lendable_directory_capability(
                self._object_directory,
                flags,
            )
        except OSError as error:
            raise RuntimeError(
                f"Cannot open the Git object quarantine: {error}"
            ) from error
        try:
            self.require_quarantine_object_directory_identity(descriptor)
            try:
                yield descriptor
            finally:
                self.require_quarantine_object_directory_identity(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def pinned_environment(self) -> Iterator[dict[str, str]]:
        """Yield a scoped Git environment routed through certified directory FDs."""
        with (
            self.pinned_quarantine_object_directory() as object_directory,
            self.pinned_persistent_object_directory() as alternate_directory,
            pin_git_object_environment(
                self._environment,
                object_directory,
                alternate_directory,
            ) as environment,
        ):
            yield environment

    def _require_open_persistent_object_directory(self, descriptor: int) -> None:
        try:
            opened = os.fstat(descriptor)
            visible = os.stat(
                self._persistent_identity.object_directory,
                follow_symlinks=False,
            )
        except OSError as error:
            raise RuntimeError(
                f"Cannot authenticate the persistent Git object store: {error}"
            ) from error
        expected = (
            self._persistent_identity.object_device,
            self._persistent_identity.object_inode,
        )
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(visible.st_mode)
            or (opened.st_dev, opened.st_ino) != expected
            or (visible.st_dev, visible.st_ino) != expected
        ):
            raise RuntimeError("The persistent Git object store identity changed")

    @contextmanager
    def pinned_persistent_object_directory(self) -> Iterator[int]:
        """Yield a pinned descriptor for the certified persistent object store."""
        self.require_persistent_identity()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = _open_lendable_directory_capability(
                self._persistent_identity.object_directory,
                flags,
            )
        except OSError as error:
            raise RuntimeError(
                f"Cannot open the persistent Git object store: {error}"
            ) from error
        try:
            self._require_open_persistent_object_directory(descriptor)
            try:
                yield descriptor
            finally:
                self._require_open_persistent_object_directory(descriptor)
                self.require_persistent_identity()
        finally:
            os.close(descriptor)


def _alternate_object_path(path: Path) -> str:
    value = str(path)
    if os.pathsep not in value and '"' not in value and "\\" not in value:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _persistent_git_object_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in _OBJECT_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    return environment


def _require_git_directory(
    path_text: str, location: str
) -> tuple[Path, os.stat_result]:
    try:
        path = Path(path_text).resolve(strict=True)
        metadata = path.stat()
    except OSError as error:
        raise RuntimeError(f"Cannot inspect Git {location}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(f"Git {location} is not a directory: {path}")
    return path, metadata


def _git_repository_object_identity(
    environment: dict[str, str],
) -> _GitRepositoryObjectIdentity:
    object_format = run_git_command(
        ["rev-parse", "--show-object-format"],
        env=environment,
        requires_index_lock=False,
    ).stdout.strip()
    if object_format not in {"sha1", "sha256"}:
        raise RuntimeError(f"Unsupported Git object format: {object_format}")
    common_directory, common_metadata = _require_git_directory(
        run_git_command(
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            env=environment,
            requires_index_lock=False,
        ).stdout.strip(),
        "common directory",
    )
    object_directory, object_metadata = _require_git_directory(
        run_git_command(
            ["rev-parse", "--path-format=absolute", "--git-path", "objects"],
            env=environment,
            requires_index_lock=False,
        ).stdout.strip(),
        "object directory",
    )
    return _GitRepositoryObjectIdentity(
        object_format=object_format,
        common_directory=common_directory,
        common_device=common_metadata.st_dev,
        common_inode=common_metadata.st_ino,
        object_directory=object_directory,
        object_device=object_metadata.st_dev,
        object_inode=object_metadata.st_ino,
    )


@contextmanager
def temporary_git_object_environment() -> Iterator[GitObjectQuarantine]:
    """Yield an object quarantine that reads, but never retains, repository objects."""
    persistent_environment = _persistent_git_object_environment()
    persistent_identity = _git_repository_object_identity(persistent_environment)
    with tempfile.TemporaryDirectory(prefix="git-stage-batch-objects-") as path:
        object_directory, object_metadata = _require_git_directory(
            path,
            "quarantine object directory",
        )
        env = persistent_environment.copy()
        env["GIT_OBJECT_DIRECTORY"] = str(object_directory)
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = _alternate_object_path(
            persistent_identity.object_directory
        )
        yield GitObjectQuarantine(
            env,
            persistent_environment,
            persistent_identity,
            object_directory,
            object_metadata.st_dev,
            object_metadata.st_ino,
            _token=_QUARANTINE_CONSTRUCTION_TOKEN,
        )


def get_empty_git_tree_object_id() -> str:
    """Return the repository-native object ID for Git's empty tree."""
    cwd = Path.cwd()
    cached = _EMPTY_TREE_OBJECT_CACHE.get(cwd)
    if cached is not None:
        return cached
    object_id = run_git_command(
        ["mktree"],
        stdin_chunks=[b""],
        requires_index_lock=False,
    ).stdout.strip()
    if not object_id:
        raise RuntimeError("git mktree produced no empty tree object")
    _EMPTY_TREE_OBJECT_CACHE[cwd] = object_id
    return object_id


def get_git_object_type(
    object_id: str,
    *,
    env: dict[str, str] | None = None,
) -> str | None:
    """Return an object's Git type, or None when it does not exist."""
    result = run_git_command(
        ["cat-file", "-t", object_id],
        check=False,
        env=env,
        requires_index_lock=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


@dataclass(frozen=True)
class GitTreeBlob:
    """One blob entry from a Git tree."""

    file_path: str
    mode: str
    blob_sha: str


@dataclass(frozen=True)
class GitTreeEntry:
    """One exact entry from a Git tree."""

    file_path: str
    mode: str
    object_type: str
    object_id: str


@dataclass(frozen=True)
class GitObjectInfo:
    """Resolved identity and storage metadata for one Git object request."""

    object_id: str
    object_type: str
    size: int


@dataclass(frozen=True)
class GitBlobStream:
    """One blob response whose content must be consumed before the next."""

    requested_name: str
    object_id: str
    size: int
    content_chunks: Iterator[bytes]


class _GitBatchOutputReader:
    """Read headers and payloads from Git's batch object protocol."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._pending = bytearray()

    def read_line(self) -> bytes:
        """Read one newline-terminated protocol line without its delimiter."""
        while True:
            line_end = self._pending.find(b"\n")
            if line_end >= 0:
                line = bytes(self._pending[:line_end])
                del self._pending[: line_end + 1]
                return line
            self._extend()

    def read_exactly(self, size: int) -> bytes:
        """Read exactly ``size`` bytes from the protocol stream."""
        return b"".join(self.read_chunks(size))

    def read_chunks(self, size: int) -> Iterator[bytes]:
        """Yield exactly ``size`` payload bytes in bounded chunks."""
        remaining = size
        while remaining:
            if not self._pending:
                self._extend()
            chunk_size = min(remaining, len(self._pending))
            yield bytes(self._pending[:chunk_size])
            del self._pending[:chunk_size]
            remaining -= chunk_size

    def finish(self) -> None:
        """Require the protocol stream to end without trailing bytes."""
        for chunk in self._chunks:
            self._pending.extend(chunk)
        if self._pending:
            raise RuntimeError("Unexpected trailing git cat-file --batch output")

    def _extend(self) -> None:
        try:
            self._pending.extend(next(self._chunks))
        except StopIteration as error:
            raise RuntimeError(
                "Unexpected end of git cat-file --batch output"
            ) from error


def create_git_blob(
    content_chunks: Iterable[bytes],
    *,
    path: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Create a git blob object from streaming content.

    Args:
        content_chunks: Iterable yielding binary content chunks to store
        path: Worktree path whose Git clean conversion should be applied
        env: Optional environment for the Git object store

    Returns:
        Repository-native object ID of the created blob object

    Raises:
        RuntimeError: If git hash-object fails or produces no output
    """
    stdout_chunks = []
    try:
        arguments = ["hash-object", "-w"]
        if path is not None:
            arguments.append(f"--path={path}")
        arguments.append("--stdin")
        for line in stream_git_command(
            arguments,
            content_chunks,
            env=env,
            requires_index_lock=False,
        ):
            stdout_chunks.append(line)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"git hash-object failed with exit code {error.returncode}: {error.stderr}"
        ) from error

    if not stdout_chunks:
        raise RuntimeError("git hash-object produced no output")

    stdout_bytes = b"".join(stdout_chunks)
    blob_sha = stdout_bytes.strip().decode("utf-8")
    return blob_sha


def create_git_blobs_from_paths(paths: Iterable[Path]) -> dict[Path, str]:
    """Create git blobs for filesystem paths with batched hash-object calls."""
    unique_paths = list(dict.fromkeys(paths))
    if not unique_paths:
        return {}

    blob_shas: dict[Path, str] = {}
    chunk_size = 512
    for offset in range(0, len(unique_paths), chunk_size):
        chunk = unique_paths[offset : offset + chunk_size]
        try:
            result = run_git_command(
                [
                    "hash-object",
                    "-w",
                    "--no-filters",
                    "--",
                    *(str(path) for path in chunk),
                ],
                requires_index_lock=False,
            )
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"git hash-object failed with exit code {error.returncode}: "
                f"{error.stderr}"
            ) from error

        chunk_shas = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        if len(chunk_shas) != len(chunk):
            raise RuntimeError("git hash-object produced an unexpected number of blobs")
        blob_shas.update(zip(chunk, chunk_shas, strict=True))

    return blob_shas


def read_git_blob(blob_sha: str) -> Iterator[bytes]:
    """Read a git blob object as a stream.

    Args:
        blob_sha: Repository-native object ID of the blob to read

    Yields:
        Binary chunks from the blob content

    Raises:
        RuntimeError: If git cat-file fails or blob doesn't exist
    """
    try:
        yield from stream_git_command(
            ["cat-file", "blob", blob_sha],
            requires_index_lock=False,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            f"git cat-file failed with exit code {error.returncode}: {error.stderr}"
        ) from error


def resolve_git_objects(
    object_names: Iterable[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, GitObjectInfo]:
    """Resolve object expressions without loading their contents."""
    unique_object_names = list(dict.fromkeys(object_names))
    if not unique_object_names:
        return {}

    payload = (
        f"{object_name}\n".encode("utf-8") for object_name in unique_object_names
    )
    result = run_git_command(
        ["cat-file", "--batch-check"],
        stdin_chunks=payload,
        text_output=False,
        env=env,
        requires_index_lock=False,
    )
    headers = result.stdout.splitlines()
    if len(headers) != len(unique_object_names):
        raise RuntimeError(
            "git cat-file --batch-check returned an unexpected response count"
        )

    resolved: dict[str, GitObjectInfo] = {}
    for requested_name, header_bytes in zip(
        unique_object_names,
        headers,
        strict=True,
    ):
        header = header_bytes.decode("ascii", errors="replace")
        parts = header.split()
        if len(parts) >= 2 and parts[-1] == "missing":
            continue
        if len(parts) != 3:
            raise RuntimeError(
                f"Unexpected git cat-file --batch-check header: {header}"
            )
        object_id, object_type, size_text = parts
        resolved[requested_name] = GitObjectInfo(
            object_id=object_id,
            object_type=object_type,
            size=int(size_text),
        )
    return resolved


def stream_git_blobs(
    blob_names: Iterable[str],
    *,
    ignore_non_blobs: bool = False,
    env: dict[str, str] | None = None,
) -> Iterator[GitBlobStream]:
    """Yield blob payload streams from one Git process.

    Each ``content_chunks`` iterator is valid until the outer iterator advances.
    Advancing drains any unread content so the batch protocol remains aligned.
    """
    unique_blob_names = list(dict.fromkeys(blob_names))
    if not unique_blob_names:
        return

    payload = (f"{blob_name}\n".encode("utf-8") for blob_name in unique_blob_names)
    reader = _GitBatchOutputReader(
        stream_git_command_bytes(
            ["cat-file", "--batch"],
            payload,
            env=env,
            requires_index_lock=False,
        )
    )
    for requested_name in unique_blob_names:
        header = reader.read_line().decode("ascii", errors="replace")
        parts = header.split()
        if len(parts) >= 2 and parts[-1] == "missing":
            continue
        if len(parts) < 3:
            raise RuntimeError(f"Unexpected git cat-file --batch header: {header}")

        object_id, object_type, size_text = parts[:3]
        size = int(size_text)
        content_chunks = reader.read_chunks(size)
        if object_type == "blob":
            yield GitBlobStream(
                requested_name=requested_name,
                object_id=object_id,
                size=size,
                content_chunks=content_chunks,
            )
        elif not ignore_non_blobs:
            raise RuntimeError(f"Unexpected git cat-file --batch header: {header}")

        for _chunk in content_chunks:
            pass
        if reader.read_exactly(1) != b"\n":
            raise RuntimeError("Unexpected git cat-file --batch object delimiter")
    reader.finish()


def read_git_blobs_as_bytes(
    blob_hashes: Iterable[str],
    *,
    ignore_non_blobs: bool = False,
) -> dict[str, bytes]:
    """Read multiple Git blobs with one cat-file process."""
    unique_blob_hashes = list(dict.fromkeys(blob_hashes))
    if not unique_blob_hashes:
        return {}

    blobs: dict[str, bytes] = {}
    for blob in stream_git_blobs(
        unique_blob_hashes,
        ignore_non_blobs=ignore_non_blobs,
    ):
        content = b"".join(blob.content_chunks)
        blobs[blob.requested_name] = content
        blobs[blob.object_id] = content

    return blobs


def list_git_tree_blobs(
    treeish: str,
    file_paths: Iterable[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, GitTreeBlob]:
    """List blob entries for paths in one tree with one ls-tree process."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    result = run_git_command(
        ["ls-tree", "-rz", treeish, "--", *unique_file_paths],
        check=False,
        text_output=False,
        env=env,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    if result.returncode != 0:
        return {}

    entries: dict[str, GitTreeBlob] = {}
    for record in nul_records(result.stdout):
        if not record:
            continue
        try:
            metadata_bytes, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        metadata = metadata_bytes.decode("ascii", errors="replace").split()
        if len(metadata) < 3 or metadata[1] != "blob":
            continue
        file_path = decode_path(path_bytes)
        entries[file_path] = GitTreeBlob(
            file_path=file_path,
            mode=metadata[0],
            blob_sha=metadata[2],
        )
    return entries


def list_git_tree_entries(
    treeish: str,
    file_paths: Iterable[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, GitTreeEntry]:
    """List exact entries for literal paths in one tree without recursion."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    entries: dict[str, GitTreeEntry] = {}
    for requested_path in unique_file_paths:
        result = run_git_command(
            ["ls-tree", "-z", "--full-tree", treeish, "--", requested_path],
            text_output=False,
            env=env,
            requires_index_lock=False,
            literal_pathspecs=True,
        )
        for record in nul_records(result.stdout):
            if not record:
                continue
            try:
                metadata_bytes, path_bytes = record.split(b"\t", 1)
            except ValueError as error:
                raise RuntimeError("Malformed git ls-tree record") from error
            file_path = decode_path(path_bytes)
            if file_path != requested_path:
                raise RuntimeError("Git ls-tree returned an unexpected path")
            metadata = metadata_bytes.decode("ascii", errors="replace").split()
            if len(metadata) != 3:
                raise RuntimeError("Malformed git ls-tree metadata")
            if file_path in entries:
                raise RuntimeError("Git ls-tree returned a duplicate path")
            entries[file_path] = GitTreeEntry(
                file_path=file_path,
                mode=metadata[0],
                object_type=metadata[1],
                object_id=metadata[2],
            )
    return entries
