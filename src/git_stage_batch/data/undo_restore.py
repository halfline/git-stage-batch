"""Undo checkpoint restoration from stored snapshot commits."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from typing import Any

from ..core.buffer import (
    write_buffer_to_path,
    write_buffer_to_working_tree_path,
)
from .repository_buffers import load_git_blob_as_buffer
from .undo_refs import list_restorable_refs
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import read_file_paths_file
from ..utils.git import (
    git_add_paths,
    git_checkout_detached,
    run_git_command,
    update_git_refs,
)
from ..utils.git_repository import get_git_repository_root_path
from ..utils.paths import get_auto_added_files_file_path


def _read_json_blob(blob_sha: str) -> dict[str, Any]:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        return json.loads(buffer.to_bytes().decode("utf-8"))


def _write_blob_to_path(blob_sha: str, target_path: Path) -> None:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        write_buffer_to_path(target_path, buffer)


def _write_blob_to_worktree_path(
    blob_sha: str,
    target_path: Path,
    *,
    mode: str,
) -> None:
    with load_git_blob_as_buffer(blob_sha) as buffer:
        write_buffer_to_working_tree_path(target_path, buffer, mode=mode)


def _tree_entries(commit: str, prefix: str) -> list[tuple[str, str, str]]:
    result = run_git_command(
        ["ls-tree", "-r", "-z", commit, prefix],
        check=False,
        text_output=False,
        requires_index_lock=False,
    )
    if result.returncode != 0 or not result.stdout:
        return []

    entries = []
    for record in result.stdout.rstrip(b"\0").split(b"\0"):
        if not record:
            continue
        meta, path_bytes = record.split(b"\t", 1)
        mode, object_type, object_sha = meta.decode("ascii").split()
        if object_type != "blob":
            continue
        entries.append(
            (
                mode,
                object_sha,
                path_bytes.decode("utf-8", errors="surrogateescape"),
            )
        )
    return entries


def read_json_from_commit(commit: str, path: str) -> dict[str, Any]:
    """Read a JSON blob from an undo snapshot commit."""
    entries = _tree_entries(commit, path)
    if not entries:
        raise CommandError(_("Undo checkpoint is missing {path}").format(path=path))
    _mode, blob_sha, _entry_path = entries[0]
    return _read_json_blob(blob_sha)


def _restore_file_mode(path: Path, mode: str) -> None:
    if mode == "120000":
        return
    current_mode = path.stat().st_mode
    if mode == "100755":
        path.chmod(current_mode | stat.S_IXUSR)
    else:
        path.chmod(current_mode & ~stat.S_IXUSR & ~stat.S_IXGRP & ~stat.S_IXOTH)


def restore_tree_prefix(commit: str, *, prefix: str, target_dir: Path) -> None:
    """Restore one tree prefix from an undo snapshot commit."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    entries = _tree_entries(commit, prefix)
    if not entries:
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for mode, blob_sha, tree_path in entries:
        relative_path = Path(tree_path).relative_to(prefix)
        target_path = target_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_blob_to_path(blob_sha, target_path)
        _restore_file_mode(target_path, mode)


def restore_refs(saved_refs: dict[str, str]) -> None:
    """Restore undo-managed refs to a saved mapping."""
    current_refs = list_restorable_refs()
    update_git_refs(
        updates=sorted(saved_refs.items()),
        deletes=sorted(
            ref_name for ref_name in current_refs if ref_name not in saved_refs
        ),
    )


def restore_worktree(commit: str, manifest: dict[str, Any]) -> None:
    """Restore worktree paths recorded in an undo checkpoint manifest."""
    repo_root = get_git_repository_root_path()
    worktree_blobs = {
        Path(tree_path).relative_to("worktree").as_posix(): (mode, blob_sha)
        for mode, blob_sha, tree_path in _tree_entries(commit, "worktree")
    }

    for entry in manifest.get("worktree_paths", []):
        file_path = entry["path"]
        target_path = repo_root / file_path
        if entry.get("kind") == "gitlink":
            worktree_oid = entry.get("worktree_oid")
            if entry.get("exists", False) and worktree_oid:
                result = git_checkout_detached(worktree_oid, cwd=file_path, check=False)
                if result.returncode != 0:
                    raise CommandError(
                        _("Failed to restore submodule pointer for {file}: {error}").format(
                            file=file_path,
                            error=result.stderr,
                        )
                    )
            continue
        if entry.get("kind") == "embedded-repo":
            continue
        if not entry.get("exists", False):
            if os.path.lexists(target_path):
                target_path.unlink()
            continue

        blob_info = worktree_blobs.get(file_path)
        if blob_info is None:
            continue
        mode, blob_sha = blob_info
        _write_blob_to_worktree_path(blob_sha, target_path, mode=mode)


def restore_intent_to_add_entries() -> None:
    """Restore intent-to-add markers tracked by the session."""
    repo_root = get_git_repository_root_path()
    for file_path in read_file_paths_file(get_auto_added_files_file_path()):
        full_path = repo_root / file_path
        if os.path.lexists(full_path):
            git_add_paths([file_path], intent_to_add=True, check=False)
