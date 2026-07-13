"""Git index and tree plumbing helpers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from .git_command import run_git_command, stream_git_command
from ..git_paths import encode_path
from .git_repository import null_object_id


@dataclass(frozen=True)
class GitIndexEntryUpdate:
    """One index-info update for a temporary Git index."""

    file_path: str
    mode: str | None = None
    blob_sha: str | None = None
    force_remove: bool = False


@contextmanager
def temp_git_index() -> Iterator[dict[str, str]]:
    """Create a temporary Git index and yield an environment that uses it."""
    temp_index = tempfile.NamedTemporaryFile(delete=False, suffix=".index")
    temp_index_path = temp_index.name
    temp_index.close()
    os.unlink(temp_index_path)

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = temp_index_path
    try:
        yield env
    finally:
        if os.path.exists(temp_index_path):
            os.unlink(temp_index_path)


def git_read_tree(treeish: str, *, env: dict[str, str] | None = None) -> None:
    """Read a Git tree into the current or provided index."""
    run_git_command(["read-tree", treeish], env=env, requires_index_lock=True)


def git_update_index(
    *,
    file_path: str,
    mode: str | None = None,
    blob_sha: str | None = None,
    force_remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Update one index entry from a blob, or force-remove it."""
    if force_remove:
        if mode is not None or blob_sha is not None:
            raise ValueError("mode and blob_sha cannot be used with force_remove=True")
        arguments = ["update-index", "--force-remove", "--", file_path]
    else:
        if mode is None or blob_sha is None:
            raise ValueError("mode and blob_sha are required unless force_remove=True")
        arguments = ["update-index", "--add", "--cacheinfo", mode, blob_sha, file_path]

    return run_git_command(
        arguments,
        check=check,
        env=env,
        requires_index_lock=True,
    )


def git_refresh_index(*, check: bool = True) -> subprocess.CompletedProcess:
    """Refresh cached index stat information from the working tree."""
    return run_git_command(
        ["update-index", "--refresh"],
        check=check,
        requires_index_lock=True,
    )


def git_update_gitlink(
    *,
    file_path: str,
    oid: str | None,
    remove: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Update one index entry that stores a submodule commit pointer."""
    if remove:
        if oid is not None:
            raise ValueError("oid cannot be used with remove=True")
        return git_update_index(
            file_path=file_path,
            force_remove=True,
            check=check,
            env=env,
        )

    if oid is None:
        raise ValueError("oid is required unless remove=True")

    return git_update_index(
        file_path=file_path,
        mode="160000",
        blob_sha=oid,
        check=check,
        env=env,
    )


def git_update_index_entries(
    entries: Iterable[GitIndexEntryUpdate],
    *,
    env: dict[str, str] | None = None,
) -> None:
    """Update several index entries through one update-index process."""
    payload_chunks: list[bytes] = []
    null_oid: bytes | None = None
    for entry in entries:
        path_bytes = encode_path(entry.file_path)
        if entry.force_remove:
            if entry.mode is not None or entry.blob_sha is not None:
                raise ValueError("mode and blob_sha cannot be used with force_remove=True")
            if null_oid is None:
                null_oid = null_object_id().encode("ascii")
            payload_chunks.extend([
                b"0 ",
                null_oid,
                b"\t",
                path_bytes,
                b"\0",
            ])
        else:
            if entry.mode is None or entry.blob_sha is None:
                raise ValueError("mode and blob_sha are required unless force_remove=True")
            payload_chunks.extend([
                entry.mode.encode("ascii"),
                b" ",
                entry.blob_sha.encode("ascii"),
                b"\t",
                path_bytes,
                b"\0",
            ])

    if not payload_chunks:
        return

    for _chunk in stream_git_command(
        ["update-index", "-z", "--index-info"],
        payload_chunks,
        env=env,
        requires_index_lock=True,
    ):
        pass


def git_write_tree(*, env: dict[str, str] | None = None) -> str:
    """Write the current or provided index as a Git tree."""
    return run_git_command(
        ["write-tree"],
        env=env,
        requires_index_lock=False,
    ).stdout.strip()


def git_commit_tree(
    tree_sha: str,
    *,
    parents: Iterable[str] = (),
    message: str,
    env: dict[str, str] | None = None,
) -> str:
    """Create a commit object from a tree and optional parents."""
    arguments = ["commit-tree", tree_sha]
    for parent in parents:
        arguments.extend(["-p", parent])
    arguments.extend(["-m", message])
    return run_git_command(arguments, env=env, requires_index_lock=False).stdout.strip()


def git_apply_to_index(
    patch_chunks: Iterable[bytes],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Apply patch chunks to the index."""
    return run_git_command(
        ["apply", "--cached", "--whitespace=nowarn"],
        stdin_chunks=patch_chunks,
        check=check,
        requires_index_lock=True,
    )


def git_add_paths(
    paths: Sequence[str],
    *,
    intent_to_add: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Stage paths, optionally as intent-to-add entries."""
    arguments = ["add"]
    if intent_to_add:
        arguments.append("-N")
    arguments.extend(["--", *paths])
    return run_git_command(
        arguments,
        check=check,
        requires_index_lock=True,
        literal_pathspecs=True,
    )


def git_add_paths_from_stdin(
    paths: Sequence[str],
    *,
    intent_to_add: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Add arbitrarily many NUL-delimited paths without an argv-sized list."""
    unique_paths = list(dict.fromkeys(paths))
    if not unique_paths:
        return subprocess.CompletedProcess(["git", "add"], 0, "", "")
    arguments = [
        "--literal-pathspecs",
        "add",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
    ]
    if intent_to_add:
        arguments.append("--intent-to-add")
    payload: list[bytes] = []
    for file_path in unique_paths:
        payload.extend((encode_path(file_path), b"\0"))
    return run_git_command(
        arguments,
        stdin_chunks=payload,
        check=check,
        requires_index_lock=True,
    )


def git_reset_paths(
    paths: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Reset paths in the index from HEAD."""
    return run_git_command(
        ["reset", "--", *paths],
        check=check,
        requires_index_lock=True,
        literal_pathspecs=True,
    )
