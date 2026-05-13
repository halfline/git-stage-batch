"""Shared helpers for batch submodule pointer operations."""

from __future__ import annotations

import shutil

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git import (
    get_git_repository_root_path,
    git_add_paths,
    git_checkout_detached,
    git_submodule_update_checkout,
    git_update_gitlink,
    run_git_command,
)


def is_batch_submodule_pointer(file_meta: dict) -> bool:
    """Return whether batch metadata describes a submodule pointer."""
    return file_meta.get("file_type") == "gitlink"


def refuse_batch_submodule_pointer_lines(action: str) -> None:
    """Reject line selection for an atomic submodule pointer batch entry."""
    exit_with_error(
        _("Cannot use --lines with submodule pointers. {action} the whole pointer instead.").format(
            action=action,
        )
    )


def _submodule_pointer_oid(
    file_path: str,
    file_meta: dict,
    field: str,
    *,
    action: str,
) -> str:
    """Return one stored submodule pointer oid, or raise a user error."""
    oid = file_meta.get(field)
    if not isinstance(oid, str):
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: missing stored commit id."
            ).format(action=action.lower(), file=file_path)
        )
    return oid


def _change_type(file_path: str, file_meta: dict, action: str) -> str:
    """Return the stored pointer change type, or raise a user error."""
    change_type = file_meta.get("change_type")
    if change_type not in {"added", "modified", "deleted"}:
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: invalid stored change type."
            ).format(action=action.lower(), file=file_path)
        )
    return change_type


def _submodule_worktree_path(file_path: str):
    return get_git_repository_root_path() / file_path


def _checkout_submodule_pointer(file_path: str, oid: str, action: str) -> None:
    """Move a clean submodule worktree to one commit."""
    status_result = run_git_command(
        ["status", "--porcelain"],
        cwd=file_path,
        check=False,
        requires_index_lock=False,
    )
    if status_result.returncode != 0:
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
            ).format(action=action, file=file_path)
        )
    if status_result.stdout.strip():
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree has local changes."
            ).format(action=action, file=file_path)
        )

    checkout_result = git_checkout_detached(oid, cwd=file_path, check=False)
    if checkout_result.returncode != 0:
        exit_with_error(
            _(
                "Failed to update submodule pointer for {file}: {error}"
            ).format(file=file_path, error=checkout_result.stderr)
        )


def _ensure_submodule_worktree(file_path: str, oid: str, action: str) -> None:
    """Ensure a submodule worktree exists, then check out one commit."""
    full_path = _submodule_worktree_path(file_path)
    if not full_path.exists():
        update_result = git_submodule_update_checkout([file_path], check=False)
        if update_result.returncode != 0:
            exit_with_error(
                _(
                    "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
                ).format(action=action, file=file_path)
            )
    _checkout_submodule_pointer(file_path, oid, action)


def _remove_submodule_worktree(file_path: str, action: str) -> None:
    """Remove a clean submodule worktree, if present."""
    full_path = _submodule_worktree_path(file_path)
    if not full_path.exists():
        return

    status_result = run_git_command(
        ["status", "--porcelain"],
        cwd=file_path,
        check=False,
        requires_index_lock=False,
    )
    if status_result.returncode != 0:
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree is unavailable."
            ).format(action=action, file=file_path)
        )
    if status_result.stdout.strip():
        exit_with_error(
            _(
                "Cannot {action} submodule pointer for {file}: submodule working tree has local changes."
            ).format(action=action, file=file_path)
        )

    if full_path.is_dir():
        shutil.rmtree(full_path)
    else:
        full_path.unlink()


def _mark_submodule_pointer_intent_to_add(file_path: str, action: str) -> None:
    """Add an intent-to-add gitlink so an added pointer appears as a live diff."""
    result = git_add_paths([file_path], intent_to_add=True, check=False)
    if result.returncode != 0:
        exit_with_error(
            _(
                "Failed to mark submodule pointer intent-to-add for {file}: {error}"
            ).format(file=file_path, error=result.stderr)
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
        exit_with_error(
            _(
                "Failed to update submodule pointer in the index for {file}: {error}"
            ).format(file=file_path, error=result.stderr)
        )


def apply_submodule_pointer_from_batch(file_path: str, file_meta: dict) -> None:
    """Apply a stored submodule pointer to the worktree."""
    change_type = _change_type(file_path, file_meta, "Apply")
    if change_type == "deleted":
        _remove_submodule_worktree(file_path, "apply")
        return

    new_oid = _submodule_pointer_oid(file_path, file_meta, "new_oid", action="Apply")
    _ensure_submodule_worktree(file_path, new_oid, "apply")
    if change_type == "added":
        _mark_submodule_pointer_intent_to_add(file_path, "apply")


def stage_submodule_pointer_from_batch(file_path: str, file_meta: dict) -> None:
    """Apply a stored submodule pointer to the worktree and index."""
    change_type = _change_type(file_path, file_meta, "Stage")
    if change_type == "deleted":
        _remove_submodule_worktree(file_path, "stage")
        _remove_submodule_pointer_from_index(file_path, "stage")
        return

    new_oid = _submodule_pointer_oid(file_path, file_meta, "new_oid", action="Stage")
    _ensure_submodule_worktree(file_path, new_oid, "stage")
    index_result = git_update_gitlink(
        file_path=file_path,
        oid=new_oid,
        check=False,
    )
    if index_result.returncode != 0:
        exit_with_error(
            _(
                "Failed to update submodule pointer in the index for {file}: {error}"
            ).format(file=file_path, error=index_result.stderr)
        )


def discard_submodule_pointer_from_batch(file_path: str, file_meta: dict) -> None:
    """Restore the baseline state for a stored submodule pointer."""
    change_type = _change_type(file_path, file_meta, "Discard")
    if change_type == "added":
        _remove_submodule_worktree(file_path, "discard")
        _remove_submodule_pointer_from_index(file_path, "discard")
        return

    old_oid = _submodule_pointer_oid(file_path, file_meta, "old_oid", action="Discard")
    _ensure_submodule_worktree(file_path, old_oid, "discard")
