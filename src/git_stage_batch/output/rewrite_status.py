"""Durable history-operation status rendering."""

from __future__ import annotations

import json

from ..git_paths import terminal_safe_shell_join, terminal_safe_text
from ..history.models import HistoryOperationInspection, HistoryOperationState
from ..i18n import _


def _status_record(
    state: HistoryOperationState | None,
    inspection: HistoryOperationInspection | None,
    *,
    active: bool,
) -> dict[str, object]:
    if state is None:
        return {
            "schema_version": 1,
            "operation": "rewrite-status",
            "active": False,
        }
    if inspection is None:
        raise TypeError("active history state requires live inspection")
    return {
        "schema_version": state.schema_version,
        "operation": "rewrite-status",
        "active": active,
        "available": True,
        "operation_id": state.operation_id,
        "phase": state.phase.value,
        "next_action": state.next_action.value,
        "source": {
            "base": state.base_commit,
            "original_tip": state.original_tip,
            "original_final_tree": state.original_final_tree,
            "branch_ref": state.branch_ref,
            "expected_branch_tip": state.expected_branch_tip,
        },
        "recovery_ref": state.recovery_ref,
        "output_ref": state.output_ref,
        "manual_recovery_command": _manual_recovery_command(state),
        "progress": {
            "planned_output_count": state.planned_output_count,
            "completed_output_count": state.completed_output_count,
            "output_commits": list(state.output_commits),
            "pending_output_commit": state.pending_output_commit,
            "pending_output_tree": state.pending_output_tree,
            "last_verified_commit": state.last_verified_commit,
            "last_verified_tree": state.last_verified_tree,
            "verification_sha256": state.verification_sha256,
        },
        "diagnostic": state.diagnostic,
        "inspection": {
            "branch_ref_matches": inspection.branch_ref_matches,
            "branch_tip_matches": inspection.branch_tip_matches,
            "index_matches": inspection.index_matches,
            "worktree_clean": inspection.worktree_clean,
            "recovery_ref_matches": inspection.recovery_ref_matches,
            "plan_matches": inspection.plan_matches,
            "output_objects_exist": inspection.output_objects_exist,
            "output_ref_matches": inspection.output_ref_matches,
            "verification_matches": inspection.verification_matches,
            "resume_ready": inspection.resume_ready,
            "blockers": list(inspection.blockers),
        },
    }


def _manual_recovery_command(state: HistoryOperationState) -> str:
    """Render the exact compare-and-swap recovery command safely."""
    return terminal_safe_shell_join(
        (
            "git",
            "update-ref",
            state.branch_ref,
            state.recovery_ref,
            state.expected_branch_tip,
        )
    )


def print_rewrite_status(
    state: HistoryOperationState | None,
    inspection: HistoryOperationInspection | None,
    *,
    active: bool,
    porcelain: bool,
) -> None:
    """Print an exact checkpoint and its current resumability."""
    if porcelain:
        print(
            json.dumps(
                _status_record(state, inspection, active=active),
                indent=2,
                ensure_ascii=True,
            )
        )
        return
    if state is None:
        print(_("No rewrite operation is active."))
        return
    if inspection is None:
        raise TypeError("active history state requires live inspection")

    print(
        (
            _("Rewrite operation {operation_id}")
            if active
            else _("Latest rewrite operation {operation_id}")
        ).format(operation_id=state.operation_id)
    )
    print(_("Phase: {phase}").format(phase=state.phase.value))
    print(
        _("Progress: {completed} of {planned} output commit(s)").format(
            completed=state.completed_output_count,
            planned=state.planned_output_count,
        )
    )
    print(_("Recovery ref: {recovery_ref}").format(recovery_ref=state.recovery_ref))
    print(
        _("Manual recovery: {command}").format(command=_manual_recovery_command(state))
    )
    if state.last_verified_commit is not None:
        print(
            _("Last verified commit: {commit}").format(
                commit=state.last_verified_commit[:12]
            )
        )
    print(_("Next action: {action}").format(action=state.next_action.value))
    if state.diagnostic is not None:
        print(
            _("Diagnostic: {diagnostic}").format(
                diagnostic=terminal_safe_text(state.diagnostic)
            )
        )
    precondition_label = (
        _("Resume preconditions") if active else _("Verification preconditions")
    )
    if inspection.resume_ready:
        print(_("{label}: ready").format(label=precondition_label))
    else:
        print(
            _("{label}: blocked ({blockers})").format(
                label=precondition_label, blockers=", ".join(inspection.blockers)
            )
        )
