"""Git object IO helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .git_command import (
    run_git_command,
    stream_git_command,
    stream_git_command_bytes,
)
from ..git_paths import decode_path, nul_records


_EMPTY_TREE_OBJECT_CACHE: dict[Path, str] = {}


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


def get_git_object_type(object_id: str) -> str | None:
    """Return an object's Git type, or None when it does not exist."""
    result = run_git_command(
        ["cat-file", "-t", object_id],
        check=False,
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


def create_git_blob(content_chunks: Iterable[bytes]) -> str:
    """Create a git blob object from streaming content.

    Args:
        content_chunks: Iterable yielding binary content chunks to store

    Returns:
        Repository-native object ID of the created blob object

    Raises:
        RuntimeError: If git hash-object fails or produces no output
    """
    stdout_chunks = []
    try:
        for line in stream_git_command(
            ["hash-object", "-w", "--stdin"],
            content_chunks,
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


def resolve_git_objects(object_names: Iterable[str]) -> dict[str, GitObjectInfo]:
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
    treeish: str, file_paths: Iterable[str]
) -> dict[str, GitTreeBlob]:
    """List blob entries for paths in one tree with one ls-tree process."""
    unique_file_paths = list(dict.fromkeys(file_paths))
    if not unique_file_paths:
        return {}

    result = run_git_command(
        ["ls-tree", "-rz", treeish, "--", *unique_file_paths],
        check=False,
        text_output=False,
        requires_index_lock=False,
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
