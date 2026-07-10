"""Git object IO helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .git_command import run_git_command, stream_git_command


@dataclass(frozen=True)
class GitTreeBlob:
    """One blob entry from a Git tree."""

    file_path: str
    mode: str
    blob_sha: str


def create_git_blob(content_chunks: Iterable[bytes]) -> str:
    """Create a git blob object from streaming content.

    Args:
        content_chunks: Iterable yielding binary content chunks to store

    Returns:
        SHA-1 hash of the created blob object

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
            f"git hash-object failed with exit code {error.returncode}: "
            f"{error.stderr}"
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
        chunk = unique_paths[offset:offset + chunk_size]
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
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        if len(chunk_shas) != len(chunk):
            raise RuntimeError(
                "git hash-object produced an unexpected number of blobs"
            )
        blob_shas.update(zip(chunk, chunk_shas, strict=True))

    return blob_shas


def read_git_blob(blob_sha: str) -> Iterator[bytes]:
    """Read a git blob object as a stream.

    Args:
        blob_sha: SHA-1 hash of the blob to read

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


def read_git_blobs_as_bytes(blob_hashes: Iterable[str]) -> dict[str, bytes]:
    """Read multiple git blobs with one cat-file process."""
    unique_blob_hashes = list(dict.fromkeys(blob_hashes))
    if not unique_blob_hashes:
        return {}

    payload = "".join(f"{blob_hash}\n" for blob_hash in unique_blob_hashes).encode(
        "utf-8"
    )
    result = run_git_command(
        ["cat-file", "--batch"],
        stdin_chunks=[payload],
        text_output=False,
        requires_index_lock=False,
    )

    data = result.stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for requested_hash in unique_blob_hashes:
        header_end = data.index(b"\n", offset)
        header = data[offset:header_end].decode("ascii", errors="replace")
        offset = header_end + 1
        parts = header.split()
        if len(parts) >= 2 and parts[1] == "missing":
            continue
        if len(parts) < 3 or parts[1] != "blob":
            raise RuntimeError(f"Unexpected git cat-file --batch header: {header}")

        object_hash = parts[0]
        size = int(parts[2])
        content = data[offset:offset + size]
        offset += size
        if offset < len(data) and data[offset:offset + 1] == b"\n":
            offset += 1
        blobs[requested_hash] = content
        blobs[object_hash] = content

    return blobs


def list_git_tree_blobs(treeish: str, file_paths: Iterable[str]) -> dict[str, GitTreeBlob]:
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
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata_bytes, path_bytes = record.split(b"\t", 1)
        except ValueError:
            continue
        metadata = metadata_bytes.decode("ascii", errors="replace").split()
        if len(metadata) < 3 or metadata[1] != "blob":
            continue
        file_path = path_bytes.decode("utf-8")
        entries[file_path] = GitTreeBlob(
            file_path=file_path,
            mode=metadata[0],
            blob_sha=metadata[2],
        )
    return entries
