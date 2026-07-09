"""Undo and redo ref bookkeeping."""

from __future__ import annotations

from ..batch.ref_names import (
    BATCH_CONTENT_REF_PREFIX,
    BATCH_STATE_REF_PREFIX,
    LEGACY_BATCH_REF_PREFIX,
)
from ..utils.git import run_git_command
from ..utils.git_refs import update_git_refs


SESSION_UNDO_STACK_REF = "refs/git-stage-batch/session/undo-stack"
SESSION_REDO_STACK_REF = "refs/git-stage-batch/session/redo-stack"
RESTORABLE_REF_PREFIXES = (
    LEGACY_BATCH_REF_PREFIX,
    BATCH_CONTENT_REF_PREFIX,
    BATCH_STATE_REF_PREFIX,
)


def list_restorable_refs() -> dict[str, str]:
    """List refs whose values are restored by undo."""
    refs: dict[str, str] = {}
    for prefix in RESTORABLE_REF_PREFIXES:
        result = run_git_command(
            ["for-each-ref", "--format=%(objectname) %(refname)", prefix],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            object_name, ref_name = line.split(None, 1)
            refs[ref_name] = object_name
    return refs


def current_stack_commit(ref_name: str) -> str | None:
    """Return the commit currently named by an undo stack ref."""
    result = run_git_command(
        ["rev-parse", "--verify", ref_name],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_undo_commit() -> str | None:
    """Return the current undo stack head."""
    return current_stack_commit(SESSION_UNDO_STACK_REF)


def current_redo_commit() -> str | None:
    """Return the current redo stack head."""
    return current_stack_commit(SESSION_REDO_STACK_REF)


def checkpoint_parent(commit: str) -> str | None:
    """Return the first parent of a stack checkpoint commit."""
    result = run_git_command(
        ["rev-parse", "--verify", f"{commit}^"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def clear_redo_history() -> None:
    """Clear redo checkpoints for the current session."""
    update_git_refs(deletes=[SESSION_REDO_STACK_REF])


def clear_undo_history() -> None:
    """Clear all undo and redo checkpoints for the current session."""
    update_git_refs(deletes=[SESSION_UNDO_STACK_REF])
    clear_redo_history()
