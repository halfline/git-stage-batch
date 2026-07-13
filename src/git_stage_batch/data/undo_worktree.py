"""Worktree state capture for undo checkpoints."""

from __future__ import annotations

import os
import stat
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from ..core.buffer import LineBuffer
from ..utils.git_command import run_git_command
from ..utils.git_index import GitIndexEntryUpdate
from ..utils.git_repository import (
    get_git_repository_root_path,
    is_git_repository_root_path,
)
from ..utils.git_object_io import create_git_blob, create_git_blobs_from_paths
from ..git_paths import decode_path, nul_records


def changed_worktree_paths() -> list[str]:
    """Return repository-relative paths whose worktree bytes may need undo."""
    paths: set[str] = set()
    commands = [
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=none",
            "--name-only",
            "-z",
            "HEAD",
        ],
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=none",
            "--cached",
            "--name-only",
            "-z",
        ],
        ["ls-files", "-z", "--others", "--exclude-standard"],
    ]
    for args in commands:
        result = run_git_command(
            args,
            check=False,
            text_output=False,
            requires_index_lock=False,
        )
        if result.returncode == 0:
            paths.update(decode_path(path) for path in nul_records(result.stdout))
    return sorted(paths)


def _gitlink_oids_from_index(paths: list[str]) -> dict[str, str]:
    """Return gitlink object IDs from the index with one Git query."""
    if not paths:
        return {}
    result = run_git_command(
        ["ls-files", "--stage", "-z", "--", *paths],
        cwd=str(get_git_repository_root_path()),
        check=False,
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    if result.returncode != 0:
        return {}
    object_ids: dict[str, str] = {}
    for record in nul_records(result.stdout):
        metadata, separator, entry_path = record.partition(b"\t")
        if not separator:
            continue
        parts = metadata.decode("ascii", errors="replace").split()
        if len(parts) >= 3 and parts[0] == "160000" and parts[2] == "0":
            object_ids[decode_path(entry_path)] = parts[1]
    return object_ids


def _gitlink_oids_from_head(paths: list[str]) -> dict[str, str]:
    """Return gitlink object IDs from HEAD with one Git query."""
    if not paths:
        return {}
    result = run_git_command(
        ["ls-tree", "-z", "HEAD", "--", *paths],
        cwd=str(get_git_repository_root_path()),
        check=False,
        text_output=False,
        requires_index_lock=False,
        literal_pathspecs=True,
    )
    if result.returncode != 0:
        return {}
    object_ids: dict[str, str] = {}
    for record in nul_records(result.stdout):
        metadata, separator, entry_path = record.partition(b"\t")
        if not separator:
            continue
        parts = metadata.decode("ascii", errors="replace").split()
        if len(parts) >= 3 and parts[0] == "160000" and parts[1] == "commit":
            object_ids[decode_path(entry_path)] = parts[2]
    return object_ids


def _worktree_commit_oid(path: str) -> str | None:
    worktree_path = get_git_repository_root_path() / path
    if not is_git_repository_root_path(worktree_path):
        return None
    result = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        cwd=str(worktree_path),
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _worktree_is_dirty(path: str) -> bool:
    result = run_git_command(
        ["status", "--porcelain"],
        cwd=str(get_git_repository_root_path() / path),
        check=False,
        requires_index_lock=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _snapshot_gitlink_path(
    path: str,
    *,
    index_oid: str | None,
    head_oid: str | None,
) -> dict[str, Any]:
    full_path = get_git_repository_root_path() / path
    worktree_exists = os.path.lexists(full_path)
    worktree_oid = _worktree_commit_oid(path)
    dirty = _worktree_is_dirty(path) if worktree_oid is not None else False
    entry = {
        "path": path,
        "kind": "gitlink",
        "exists": worktree_exists,
        "mode": "160000",
        "index_oid": index_oid,
        "head_oid": head_oid,
        "worktree_oid": worktree_oid,
        "dirty": dirty,
        "blob": None,
    }
    if worktree_exists and (head_oid is None or worktree_oid is None or dirty):
        entry["archive"] = True
        entry["storage_mode"] = "100644"
        entry["blob"] = _create_directory_archive_blob(
            full_path
        )
    return entry


def _snapshot_embedded_repo_path(path: str) -> dict[str, Any]:
    full_path = get_git_repository_root_path() / path
    worktree_oid = _worktree_commit_oid(path)
    return {
        "path": path,
        "kind": "embedded-repo",
        "exists": os.path.lexists(full_path),
        "mode": "160000",
        "index_oid": None,
        "head_oid": None,
        "worktree_oid": worktree_oid,
        "dirty": _worktree_is_dirty(path) if worktree_oid is not None else False,
        "archive": True,
        "storage_mode": "100644",
        "blob": _create_directory_archive_blob(
            full_path
        ),
    }


def _create_directory_archive_blob(path: Path) -> str:
    """Store a complete nested-repository directory as one recovery blob."""
    with tempfile.NamedTemporaryFile() as archive_file:
        with tarfile.open(fileobj=archive_file, mode="w") as archive:
            archive.add(path, arcname=".", recursive=True)
        archive_file.flush()
        with LineBuffer.from_path(Path(archive_file.name)) as archive_buffer:
            return create_git_blob(archive_buffer.byte_chunks())


def snapshot_worktree_paths(paths: list[str]) -> list[dict[str, Any]]:
    """Return before-image entries for repository-relative worktree paths."""
    repo_root = get_git_repository_root_path()
    unique_paths = sorted(set(paths))
    index_gitlinks = _gitlink_oids_from_index(unique_paths)
    head_gitlinks = _gitlink_oids_from_head(unique_paths)
    normal_paths: list[Path] = []
    modes_by_path: dict[str, str] = {}
    for file_path in unique_paths:
        full_path = repo_root / file_path
        if file_path in index_gitlinks or file_path in head_gitlinks:
            continue
        if full_path.is_dir() and (full_path / ".git").exists():
            continue
        if os.path.lexists(full_path):
            mode = file_mode_for_path(full_path)
            modes_by_path[file_path] = mode
            if mode != "120000":
                normal_paths.append(full_path)
    normal_blobs = create_git_blobs_from_paths(normal_paths)

    worktree_paths: list[dict[str, Any]] = []
    for file_path in unique_paths:
        full_path = repo_root / file_path
        index_oid = index_gitlinks.get(file_path)
        head_oid = head_gitlinks.get(file_path)
        if index_oid is not None or head_oid is not None:
            worktree_paths.append(
                _snapshot_gitlink_path(
                    file_path,
                    index_oid=index_oid,
                    head_oid=head_oid,
                )
            )
            continue
        if full_path.is_dir() and (full_path / ".git").exists():
            worktree_paths.append(_snapshot_embedded_repo_path(file_path))
            continue
        if os.path.lexists(full_path):
            mode = modes_by_path[file_path]
            worktree_paths.append(
                {
                    "path": file_path,
                    "exists": True,
                    "mode": mode,
                    "blob": (
                        create_blob_from_worktree_path(full_path, mode=mode)
                        if mode == "120000"
                        else normal_blobs[full_path]
                    ),
                }
            )
        else:
            worktree_paths.append(
                {
                    "path": file_path,
                    "exists": False,
                    "mode": "100644",
                    "blob": None,
                }
            )

    return worktree_paths


def _create_blob_from_path(path: Path) -> str:
    with LineBuffer.from_path(path) as buffer:
        return create_git_blob(buffer.byte_chunks())


def create_blob_from_worktree_path(path: Path, *, mode: str) -> str:
    """Create a Git blob from a normal file or symlink worktree path."""
    if mode == "120000":
        return create_git_blob([os.readlink(os.fsencode(path))])
    return _create_blob_from_path(path)


def index_update_from_path(
    *,
    index_path: str,
    source_path: Path,
    mode: str,
) -> GitIndexEntryUpdate:
    """Return an index update for a worktree path."""
    return GitIndexEntryUpdate(
        file_path=index_path,
        mode=mode,
        blob_sha=create_blob_from_worktree_path(source_path, mode=mode),
    )


def file_mode_for_path(path: Path) -> str:
    """Return the Git file mode matching a worktree path."""
    try:
        file_status = path.lstat()
    except OSError:
        return "100644"
    if stat.S_ISLNK(file_status.st_mode):
        return "120000"
    return "100755" if file_status.st_mode & stat.S_IXUSR else "100644"
