"""Durable history-operation status rendering."""

from __future__ import annotations

import json

from ..git_paths import terminal_safe_text
from ..history.models import HistoryOperationInspection, HistoryOperationState
from ..i18n import _


def _status_record(
    state: HistoryOperationState | None,
    inspection: HistoryOperationInspection | None,
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
        "active": True,
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
        "manual_recovery_command": (
            f"git update-ref {state.branch_ref} {state.recovery_ref} "
            f"{state.expected_branch_tip}"
        ),
        "progress": {
            "planned_output_count": state.planned_output_count,
            "completed_output_count": state.completed_output_count,
            "output_commits": list(state.output_commits),
            "last_verified_commit": state.last_verified_commit,
            "last_verified_tree": state.last_verified_tree,
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
            "resume_ready": inspection.resume_ready,
            "blockers": list(inspection.blockers),
        },
    }


def print_rewrite_status(
    state: HistoryOperationState | None,
    inspection: HistoryOperationInspection | None,
    *,
    porcelain: bool,
) -> None:
    """Print an exact checkpoint and its current resumability."""
    if porcelain:
        print(
            json.dumps(
                _status_record(state, inspection),
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
        _("Rewrite operation {operation_id}").format(
            operation_id=state.operation_id
        )
    )
    print(_("Phase: {phase}").format(phase=state.phase.value))
    print(
        _("Progress: {completed} of {planned} output commit(s)").format(
            completed=state.completed_output_count,
            planned=state.planned_output_count,
        )
    )
    print(
        _("Recovery ref: {recovery_ref}").format(
            recovery_ref=state.recovery_ref
        )
    )
    print(
        _("Manual recovery: {command}").format(
            command=(
                f"git update-ref {state.branch_ref} {state.recovery_ref} "
                f"{state.expected_branch_tip}"
            )
        )
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
    if inspection.resume_ready:
        print(_("Resume preconditions: ready"))
    else:
        print(
            _("Resume preconditions: blocked ({blockers})").format(
                blockers=", ".join(inspection.blockers)
            )
        )
