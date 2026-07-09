"""Worktree state capture for undo checkpoints."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from ..core.buffer import LineBuffer
from ..utils.git import run_git_command
from ..utils.git_index import GitIndexEntryUpdate
from ..utils.git_repository import get_git_repository_root_path
from ..utils.git_object_io import create_git_blob


def changed_worktree_paths() -> list[str]:
    """Return repository-relative paths whose worktree bytes may need undo."""
    paths: set[str] = set()
    commands = [
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--ignore-submodules=none",
            "--name-only",
            "HEAD",
        ],
        [
            "-c",
            "diff.ignoreSubmodules=none",
            "diff",
            "--ignore-submodules=none",
            "--cached",
            "--name-only",
        ],
        ["ls-files", "--others", "--exclude-standard"],
    ]
    for args in commands:
        result = run_git_command(args, check=False, requires_index_lock=False)
        if result.returncode == 0:
            paths.update(line for line in result.stdout.splitlines() if line)
    return sorted(paths)


def _gitlink_oid_from_index(path: str) -> str | None:
    result = run_git_command(
        ["ls-files", "--stage", "--", path],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "160000":
            return parts[1]
    return None


def _gitlink_oid_from_head(path: str) -> str | None:
    result = run_git_command(
        ["ls-tree", "HEAD", "--", path],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        metadata, _separator, _entry_path = line.partition("\t")
        parts = metadata.split()
        if len(parts) >= 3 and parts[0] == "160000" and parts[1] == "commit":
            return parts[2]
    return None


def _worktree_commit_oid(path: str) -> str | None:
    result = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        cwd=path,
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _worktree_is_dirty(path: str) -> bool:
    result = run_git_command(
        ["status", "--porcelain"],
        cwd=path,
        check=False,
        requires_index_lock=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _is_gitlink_path(path: str) -> bool:
    return _gitlink_oid_from_index(path) is not None or _gitlink_oid_from_head(path) is not None


def _snapshot_gitlink_path(path: str) -> dict[str, Any]:
    index_oid = _gitlink_oid_from_index(path)
    head_oid = _gitlink_oid_from_head(path)
    worktree_oid = _worktree_commit_oid(path)
    return {
        "path": path,
        "kind": "gitlink",
        "exists": index_oid is not None or head_oid is not None or worktree_oid is not None,
        "mode": "160000",
        "index_oid": index_oid,
        "head_oid": head_oid,
        "worktree_oid": worktree_oid,
        "dirty": _worktree_is_dirty(path) if worktree_oid is not None else False,
        "blob": None,
    }


def _snapshot_embedded_repo_path(path: str) -> dict[str, Any]:
    worktree_oid = _worktree_commit_oid(path)
    return {
        "path": path,
        "kind": "embedded-repo",
        "exists": worktree_oid is not None,
        "mode": "160000",
        "index_oid": None,
        "head_oid": None,
        "worktree_oid": worktree_oid,
        "dirty": _worktree_is_dirty(path) if worktree_oid is not None else False,
        "blob": None,
    }


def snapshot_worktree_paths(paths: list[str]) -> list[dict[str, Any]]:
    """Return before-image entries for repository-relative worktree paths."""
    repo_root = get_git_repository_root_path()
    worktree_paths: list[dict[str, Any]] = []
    for file_path in sorted(set(paths)):
        full_path = repo_root / file_path
        if _is_gitlink_path(file_path):
            worktree_paths.append(_snapshot_gitlink_path(file_path))
            continue
        if full_path.is_dir() and (full_path / ".git").exists():
            worktree_paths.append(_snapshot_embedded_repo_path(file_path))
            continue
        if os.path.lexists(full_path):
            mode = file_mode_for_path(full_path)
            worktree_paths.append(
                {
                    "path": file_path,
                    "exists": True,
                    "mode": mode,
                    "blob": create_blob_from_worktree_path(
                        full_path,
                        mode=mode,
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
