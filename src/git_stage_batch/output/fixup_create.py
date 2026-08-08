"""Render staged fixup plans and creation results."""

from __future__ import annotations

import json

from ..fixup.models import (
    FixupCreatePlan,
    FixupCreateResult,
    FixupUnitAnalysis,
)
from ..fixup.records import fixup_analysis_record
from ..git_paths import display_path, terminal_safe_text
from ..i18n import _


def fixup_reason_text(analysis: FixupUnitAnalysis) -> str:
    messages = {
        "lineage-and-placement-agree": _("lineage and placement agree"),
        "unique-lineage-and-free-placement": _(
            "unique lineage; patch commutes through the range"
        ),
        "placement-barrier-without-lineage": _(
            "mechanical placement only; semantic target is unproven"
        ),
        "lineage-and-placement-disagree": _(
            "lineage and placement identify different commits"
        ),
        "multiple-lineage-candidates": _("selected lines have multiple owners"),
        "no-target-evidence": _("no target evidence in the selected range"),
        "whole-file-addition": _("whole-file additions are not supported yet"),
        "whole-file-deletion": _("whole-file deletions are not supported yet"),
        "binary-change": _("binary changes are not supported yet"),
        "rename": _("renames are not supported yet"),
        "rename-with-content": _(
            "content changes across renames are not supported yet"
        ),
        "file-mode-change": _("file-mode changes are not supported yet"),
        "gitlink-change": _("gitlink changes are not supported yet"),
        "non-regular-text-file": _("the selected path is not a regular text file"),
        "unit-has-no-text-patch": _("the unit has no textual patch"),
        "staged-unit-no-longer-applies-to-head": _(
            "the staged unit no longer applies to the frozen HEAD"
        ),
        "range-tree-chain-changed": _("the frozen range tree chain changed"),
        "placement-analysis-failed": _("placement analysis failed"),
    }
    return messages.get(analysis.reason_code, analysis.reason_code)


def _unit_location(analysis: FixupUnitAnalysis) -> str:
    unit = analysis.unit
    if unit.old_start is None or unit.new_start is None:
        return unit.kind
    return (
        f"@@ -{unit.old_start},{unit.old_len or 0} "
        f"+{unit.new_start},{unit.new_len or 0} @@"
    )


def _porcelain_record(
    plan: FixupCreatePlan,
    *,
    dry_run: bool,
    result: FixupCreateResult | None,
) -> dict[str, object]:
    created_by_target = (
        {created.target: created for created in result.created}
        if result is not None
        else {}
    )
    return {
        "schema_version": plan.schema_version,
        "operation": "fixup-create",
        "dry_run": dry_run,
        "range": {
            "base": plan.commit_range.base_commit,
            "head": plan.commit_range.head_commit,
            "commits_newest_first": list(
                plan.commit_range.commits_newest_first
            ),
        },
        "source": {
            "object_format": plan.object_format,
            "head_tree": plan.head_tree,
            "index_tree": plan.index_tree,
        },
        "units": [fixup_analysis_record(analysis) for analysis in plan.units],
        "assignments": [
            {
                "unit_id": assignment.unit_id,
                "target": assignment.target,
                "basis": assignment.basis,
            }
            for assignment in plan.assignments
        ],
        "groups": [
            {
                "target": group.target,
                "subject": group.subject,
                "fixup_subject": group.fixup_subject,
                "hash_qualified": group.hash_qualified,
                "unit_ids": list(group.unit_ids),
                "created_commit": (
                    created_by_target[group.target].commit
                    if group.target in created_by_target
                    else None
                ),
            }
            for group in plan.groups
        ],
        "summary": {
            "total_units": len(plan.units),
            "eligible_units": len(plan.automatic_units),
            "assigned_units": len(plan.assigned_units),
            "remaining_units": len(plan.remaining_units),
            "created_commits": len(result.created) if result is not None else 0,
        },
        "recovery_ref": result.recovery_ref if result is not None else None,
    }


def print_fixup_create_output(
    plan: FixupCreatePlan,
    *,
    dry_run: bool,
    porcelain: bool,
    result: FixupCreateResult | None,
) -> None:
    """Print one complete staged-fixup plan or execution result."""
    if porcelain:
        print(
            json.dumps(
                _porcelain_record(plan, dry_run=dry_run, result=result),
                indent=2,
            )
        )
        return

    print(
        _("Fixup plan for {base}..{head}:").format(
            base=plan.commit_range.base_commit[:12],
            head=plan.commit_range.head_commit[:12],
        )
    )
    assignments_by_id = {
        assignment.unit_id: assignment for assignment in plan.assignments
    }
    for analysis in plan.units:
        evidence_target = (
            analysis.target[:12] if analysis.target is not None else _("unassigned")
        )
        assignment = assignments_by_id.get(analysis.unit.unit_id)
        assignment_target = (
            assignment.target[:12] if assignment is not None else _("unassigned")
        )
        assignment_basis = assignment.basis if assignment is not None else "none"
        print(
            "  {unit}  {path} {location} evidence {evidence} [{status}] "
            "-> {target} [{basis}]".format(
                unit=analysis.unit.unit_id[:12],
                path=display_path(analysis.unit.path),
                location=_unit_location(analysis),
                evidence=evidence_target,
                status=analysis.status,
                target=assignment_target,
                basis=assignment_basis,
            )
        )
        print(f"    {fixup_reason_text(analysis)}")

    for group in plan.groups:
        print(
            _("Target {target} {subject}: {count} unit(s)").format(
                target=group.target[:12],
                subject=terminal_safe_text(group.subject),
                count=len(group.unit_ids),
            )
        )

    if dry_run:
        print(_("Dry run: no commits were created."))
    elif result is not None:
        for created in result.created:
            print(
                _("Created {commit} as a fixup for {target}.").format(
                    commit=created.commit[:12],
                    target=created.target[:12],
                )
            )
        print(
            _("Recovery ref: {recovery_ref}").format(recovery_ref=result.recovery_ref)
        )

    if plan.remaining_units:
        print(
            _("{count} unit(s) remain staged.").format(count=len(plan.remaining_units))
        )
