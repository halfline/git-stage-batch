"""Reachability roots for objects promised by session recovery metadata."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.git_refs import update_git_refs


RECOVERY_ANCHOR_REF_PREFIX = "refs/git-stage-batch/session/anchors/"


def recovery_anchor_ref(object_name: str) -> str:
    """Return the session-lifetime anchor ref for one Git object."""
    return f"{RECOVERY_ANCHOR_REF_PREFIX}{object_name}"


def _existing_object_names(object_names: Iterable[str | None]) -> set[str]:
    return {name for name in object_names if isinstance(name, str) and name}


def state_recovery_objects(state: Mapping[str, Any]) -> set[str]:
    """Collect object IDs serialized in one undo state mapping."""
    object_names = _existing_object_names(
        [state.get("head"), state.get("index_tree"), *state.get("refs", {}).values()]
    )
    for entry in state.get("index_entries", {}).values():
        if isinstance(entry, Mapping) and entry.get("mode") != "160000":
            object_names.update(_existing_object_names([entry.get("object_id")]))
    for entry in state.get("worktree_paths", []):
        if not isinstance(entry, Mapping):
            continue
        # A gitlink's worktree_oid belongs to the nested repository, not this
        # repository's object database, and cannot be the target of a
        # superproject ref. Blob IDs are owned by the current repository.
        object_names.update(_existing_object_names([entry.get("blob")]))
    return object_names


def anchor_recovery_objects(object_names: Iterable[str | None]) -> dict[str, str]:
    """Create session refs that keep the supplied objects reachable."""
    objects = sorted(_existing_object_names(object_names))
    if not objects:
        return {}
    anchors = {recovery_anchor_ref(object_name): object_name for object_name in objects}
    update_git_refs(updates=anchors.items())
    return anchors


def anchor_recovery_state(state: Mapping[str, Any]) -> dict[str, str]:
    """Anchor every object serialized by an undo/redo state mapping."""
    return anchor_recovery_objects(state_recovery_objects(state))


def validate_recovery_objects(
    object_names: Iterable[str | None],
    *,
    anchors: Mapping[str, str] | None = None,
) -> None:
    """Verify recovery objects and any recorded anchor refs before mutation."""
    expected_objects = sorted(_existing_object_names(object_names))
    for object_name in expected_objects:
        result = run_git_command(
            ["cat-file", "-e", object_name],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0:
            raise CommandError(
                _(
                    "Recovery object {object_name} is no longer available. "
                    "The checkpoint was created without a durable reachability root "
                    "or the repository's recovery refs were removed."
                ).format(object_name=object_name)
            )

    for ref_name, object_name in sorted((anchors or {}).items()):
        result = run_git_command(
            ["rev-parse", "--verify", ref_name],
            check=False,
            requires_index_lock=False,
        )
        if result.returncode != 0 or result.stdout.strip() != object_name:
            raise CommandError(
                _(
                    "Recovery anchor {ref_name} is missing or does not name "
                    "the expected object {object_name}."
                ).format(ref_name=ref_name, object_name=object_name)
            )


def validate_recovery_state(state: Mapping[str, Any]) -> None:
    """Validate a current or legacy state mapping before restoring it."""
    validate_recovery_objects(
        state_recovery_objects(state),
        anchors=state.get("recovery_anchors")
        if isinstance(state.get("recovery_anchors"), Mapping)
        else None,
    )


def clear_recovery_anchors() -> None:
    """Delete every object anchor owned by the completed session."""
    result = run_git_command(
        ["for-each-ref", "--format=%(refname)", RECOVERY_ANCHOR_REF_PREFIX],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode != 0:
        return
    update_git_refs(deletes=[line for line in result.stdout.splitlines() if line])
