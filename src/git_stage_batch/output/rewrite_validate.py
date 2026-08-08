"""History-plan validation reports."""

from __future__ import annotations

import json

from ..history.models import HistoryPlanDocument
from ..history.records import history_safety_record
from ..i18n import _, ngettext


def _validation_record(document: HistoryPlanDocument) -> dict[str, object]:
    reword_count = sum(
        output.operation == "REWORD" for output in document.plan.outputs
    )
    integration_count = sum(
        output.operation == "INTEGRATE" for output in document.plan.outputs
    )
    split_count = sum(
        output.operation == "SPLIT" for output in document.plan.outputs
    )
    reorder_count = sum(
        output.operation == "REORDER" for output in document.plan.outputs
    )
    blocked_dependencies = sum(
        dependency.barrier == "BLOCKED"
        for dependency in document.snapshot.dependencies
    )
    unknown_dependencies = sum(
        dependency.barrier == "UNKNOWN"
        for dependency in document.snapshot.dependencies
    )
    return {
        "schema_version": document.schema_version,
        "operation": "rewrite-validate",
        "valid": True,
        "range": {
            "base": document.snapshot.base_commit,
            "tip": document.snapshot.tip_commit,
            "final_tree": document.snapshot.final_tree,
        },
        "summary": {
            "source_commits": len(document.snapshot.commits),
            "output_commits": len(document.plan.outputs),
            "reworded_commits": reword_count,
            "integrated_outputs": integration_count,
            "split_outputs": split_count,
            "reordered_outputs": reorder_count,
            "patch_units": sum(
                len(commit.units) for commit in document.snapshot.commits
            ),
            "dependency_units": len(document.snapshot.dependencies),
            "blocked_dependencies": blocked_dependencies,
            "unknown_dependencies": unknown_dependencies,
            "source_signatures": sum(
                len(commit.signatures) for commit in document.snapshot.commits
            ),
        },
        "safety": history_safety_record(document.safety),
    }


def print_rewrite_validation(
    document: HistoryPlanDocument,
    *,
    porcelain: bool,
) -> None:
    """Print successful semantic and mechanical plan validation."""
    if porcelain:
        print(json.dumps(_validation_record(document), indent=2, ensure_ascii=True))
        return
    print(_("Rewrite plan is valid."))
    print(
        ngettext(
            "{count} source commit is conserved.",
            "{count} source commits are conserved.",
            len(document.snapshot.commits),
        ).format(count=len(document.snapshot.commits))
    )
    if document.safety.mutation_ready:
        print(_("Mutation preconditions: ready"))
    else:
        print(
            _("Mutation preconditions: blocked ({blockers})").format(
                blockers=", ".join(document.safety.blockers)
            )
        )
