"""Rendering for rewrite apply, continue, abort, and verify actions."""

from __future__ import annotations

import json

from ..history.models import HistoryOperationState, HistoryPhase, HistoryVerification
from ..i18n import _, ngettext


def _operation_record(
    action: str,
    state: HistoryOperationState,
    verification: HistoryVerification | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": f"rewrite-{action}",
        "operation_id": state.operation_id,
        "phase": state.phase.value,
        "next_action": state.next_action.value,
        "branch_ref": state.branch_ref,
        "base_commit": state.base_commit,
        "original_tip": state.original_tip,
        "output_tip": state.output_commits[-1] if state.output_commits else None,
        "final_tree": state.original_final_tree,
        "recovery_ref": state.recovery_ref,
        "output_ref": state.output_ref,
        "planned_output_count": state.planned_output_count,
        "completed_output_count": state.completed_output_count,
        "verification_sha256": state.verification_sha256,
        "verified": (verification is not None or state.verification_sha256 is not None),
        "removed_signatures": (
            [
                {
                    "source_commit": source_commit,
                    "header": signature.header,
                    "sha256": signature.sha256,
                }
                for source_commit, signature in verification.removed_signatures
            ]
            if verification is not None
            else None
        ),
    }


def print_rewrite_operation(
    action: str,
    state: HistoryOperationState,
    *,
    verification: HistoryVerification | None = None,
    porcelain: bool,
) -> None:
    """Render one mutation or independent verification result."""
    if porcelain:
        print(
            json.dumps(
                _operation_record(action, state, verification),
                indent=2,
                ensure_ascii=True,
            )
        )
        return

    print(
        _("Rewrite operation {operation_id}: {phase}").format(
            operation_id=state.operation_id,
            phase=state.phase.value,
        )
    )
    if state.output_commits:
        print(_("Output tip: {commit}").format(commit=state.output_commits[-1]))
    if action == "abort" and state.phase is HistoryPhase.COMPLETE:
        print(_("Rewrite operation was already complete; abort made no changes."))
    elif state.phase is HistoryPhase.ABORTED:
        print(_("Original branch tip restored."))
    elif verification is not None or state.verification_sha256 is not None:
        print(_("Verified final tree: {tree}").format(tree=state.original_final_tree))
    else:
        print(_("Next action: {action}").format(action=state.next_action.value))
    print(_("Recovery ref: {recovery_ref}").format(recovery_ref=state.recovery_ref))
    if verification is not None and verification.removed_signatures:
        count = len(verification.removed_signatures)
        print(
            ngettext(
                "Removed {count} invalidated source signature.",
                "Removed {count} invalidated source signatures.",
                count,
            ).format(count=count)
        )
