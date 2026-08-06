"""Shared helpers for batch submodule pointer operations."""

from __future__ import annotations

import shutil
from pathlib import Path

from .state.metadata_types import BatchFileMetadataDict
from ..exceptions import CommandError
from ..git_paths import display_path
from ..i18n import _, pgettext
from ..utils.git_command import run_git_command
from ..utils.git_worktree import (
    git_checkout_detached,
    git_submodule_update_checkout,
)
from ..utils.git_index import (
    git_add_paths,
    git_update_gitlink,
)
from ..utils.git_repository import (
    get_git_repository_root_path,
    is_git_repository_root_path,
)


def is_batch_submodule_pointer(file_meta: BatchFileMetadataDict) -> bool:
    """Return whether batch metadata describes a submodule pointer."""
    return file_meta.get("file_type") == "gitlink"


def refuse_batch_submodule_pointer_lines() -> None:
    """Reject line selection for an atomic submodule pointer batch entry."""
    raise CommandError(
        _(
            "Cannot use --lines with submodule pointers. "
            "Select the whole pointer instead."
        )
    )


def _submodule_pointer_oid(
    file_path: str,
    file_meta: BatchFileMetadataDict,
    field: str,
    *,
    action: str,
) -> str:
    """Return one stored submodule pointer oid, or raise a user error."""
    oid = file_meta.get(field)
    if not isinstance(oid, str):
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: missing stored commit id."
            ).format(action=action, file=display_path(file_path))
        )
    return oid


def _change_type(
    file_path: str,
    file_meta: BatchFileMetadataDict,
    action: str,
) -> str:
    """Return the stored pointer change type, or raise a user error."""
    change_type = file_meta.get("change_type")
    if change_type not in {"added", "modified", "deleted"}:
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: invalid stored change type."
            ).format(action=action, file=display_path(file_path))
        )
    return change_type


def _submodule_worktree_path(file_path: str) -> Path:
    return get_git_repository_root_path() / file_path


def _require_submodule_worktree(file_path: str, action: str) -> Path:
    """Return an independently rooted submodule worktree or fail closed."""
    full_path = _submodule_worktree_path(file_path)
    if not is_git_repository_root_path(full_path):
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: "
                "the path is not a standalone Git repository."
            ).format(action=action, file=display_path(file_path))
        )
    return full_path


def _checkout_submodule_pointer(file_path: str, oid: str, action: str) -> None:
    """Move a clean submodule worktree to one commit."""
    full_path = _require_submodule_worktree(file_path, action)
    status_result = run_git_command(
        ["status", "--porcelain"],
        cwd=str(full_path),
        check=False,
        requires_index_lock=False,
    )
    if status_result.returncode != 0:
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
            ).format(action=action, file=display_path(file_path))
        )
    if status_result.stdout.strip():
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree has local changes."
            ).format(action=action, file=display_path(file_path))
        )

    checkout_result = git_checkout_detached(
        oid,
        cwd=str(full_path),
        check=False,
    )
    if checkout_result.returncode != 0:
        raise CommandError(
            _(
                "Failed to update submodule pointer for {file}: {error}"
            ).format(file=display_path(file_path), error=checkout_result.stderr)
        )


def _ensure_submodule_worktree(file_path: str, oid: str, action: str) -> None:
    """Ensure a submodule worktree exists, then check out one commit."""
    full_path = _submodule_worktree_path(file_path)
    if not full_path.exists():
        update_result = git_submodule_update_checkout(
            [file_path],
            cwd=str(get_git_repository_root_path()),
            check=False,
        )
        if update_result.returncode != 0:
            raise CommandError(
                _(
                    "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
                ).format(action=action, file=display_path(file_path))
            )
    _checkout_submodule_pointer(file_path, oid, action)


def _remove_submodule_worktree(file_path: str, action: str) -> None:
    """Remove a clean submodule worktree, if present."""
    full_path = _submodule_worktree_path(file_path)
    if not full_path.exists():
        return

    full_path = _require_submodule_worktree(file_path, action)

    status_result = run_git_command(
        ["status", "--porcelain"],
        cwd=str(full_path),
        check=False,
        requires_index_lock=False,
    )
    if status_result.returncode != 0:
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
            ).format(action=action, file=display_path(file_path))
        )
    if status_result.stdout.strip():
        raise CommandError(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree has local changes."
            ).format(action=action, file=display_path(file_path))
        )

    if full_path.is_dir():
        shutil.rmtree(full_path)
    else:
        full_path.unlink()


def _mark_submodule_pointer_intent_to_add(file_path: str, action: str) -> None:
    """Add an intent-to-add gitlink so an added pointer appears as a live diff."""
    result = git_add_paths([file_path], intent_to_add=True, check=False)
    if result.returncode != 0:
        raise CommandError(
            _(
                "Failed to mark submodule pointer intent-to-add for {file}: {error}"
            ).format(file=display_path(file_path), error=result.stderr)
        )


def _remove_submodule_pointer_from_index(file_path: str, action: str) -> None:
    """Remove a gitlink or intent-to-add gitlink from the index."""
    result = git_update_gitlink(
        file_path=file_path,
        oid=None,
        remove=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(
            _(
                "Failed to update submodule pointer in the index for {file}: {error}"
            ).format(file=display_path(file_path), error=result.stderr)
        )


def apply_submodule_pointer_from_batch(
    file_path: str,
    file_meta: BatchFileMetadataDict,
) -> None:
    """Apply a stored submodule pointer to the worktree."""
    action = pgettext("submodule action verb", "apply")
    change_type = _change_type(file_path, file_meta, action)
    if change_type == "deleted":
        _remove_submodule_worktree(file_path, action)
        return

    new_oid = _submodule_pointer_oid(file_path, file_meta, "new_oid", action=action)
    _ensure_submodule_worktree(file_path, new_oid, action)
    if change_type == "added":
        _mark_submodule_pointer_intent_to_add(file_path, action)


def stage_submodule_pointer_from_batch(
    file_path: str,
    file_meta: BatchFileMetadataDict,
) -> None:
    """Apply a stored submodule pointer to the worktree and index."""
    action = pgettext("submodule action verb", "stage")
    change_type = _change_type(file_path, file_meta, action)
    if change_type == "deleted":
        _remove_submodule_worktree(file_path, action)
        _remove_submodule_pointer_from_index(file_path, action)
        return

    new_oid = _submodule_pointer_oid(file_path, file_meta, "new_oid", action=action)
    _ensure_submodule_worktree(file_path, new_oid, action)
    index_result = git_update_gitlink(
        file_path=file_path,
        oid=new_oid,
        check=False,
    )
    if index_result.returncode != 0:
        raise CommandError(
            _(
                "Failed to update submodule pointer in the index for {file}: {error}"
            ).format(file=display_path(file_path), error=index_result.stderr)
        )


def discard_submodule_pointer_from_batch(
    file_path: str,
    file_meta: BatchFileMetadataDict,
) -> None:
    """Restore the baseline state for a stored submodule pointer."""
    action = pgettext("submodule action verb", "discard")
    change_type = _change_type(file_path, file_meta, action)
    if change_type == "added":
        _remove_submodule_worktree(file_path, action)
        _remove_submodule_pointer_from_index(file_path, action)
        return

    old_oid = _submodule_pointer_oid(file_path, file_meta, "old_oid", action=action)
    _ensure_submodule_worktree(file_path, old_oid, action)
