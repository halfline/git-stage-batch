"""Advisory checks for one output and the frozen sources it selects."""

from __future__ import annotations

from .models import HistoryCommitSnapshot, HistoryPatchUnit, HistoryPlannedCommit
from .plan_diagnostics import PlanDiagnosticCollector
from .plan_source_index import PlanSourceIndex


_UNSUPPORTED_RESOLUTION_KINDS = frozenset({"rename", "file-type", "gitlink"})
_UNSUPPORTED_RESOLUTION_REASONS = frozenset(
    {"rename-with-content", "file-type-with-content"}
)


def lint_plan_output(
    index: int,
    output: HistoryPlannedCommit,
    source_index: PlanSourceIndex,
    report: PlanDiagnosticCollector,
) -> bool:
    """Report reference errors and return whether global checks remain safe."""
    source_by_id = source_index.source_by_id
    source_positions = source_index.source_positions
    unit_by_id = source_index.unit_by_id
    unit_positions = source_index.unit_positions
    valid = True
    location = f"plan.outputs[{index}]"
    sources = tuple(
        source_by_id[source_id]
        for source_id in output.source_commits
        if source_id in source_by_id
    )
    unknown_sources = tuple(
        source_id
        for source_id in output.source_commits
        if source_id not in source_by_id
    )
    if not output.source_commits:
        report.add(
            "sources-empty",
            "source_commits must not be empty",
            f"{location}.source_commits",
            output_index=index,
        )
        valid = False
    if unknown_sources:
        report.add(
            "source-unknown",
            "source_commits contains an unknown commit",
            f"{location}.source_commits",
            output_index=index,
            source_commits=unknown_sources,
        )
        valid = False
    if len(set(output.source_commits)) != len(output.source_commits):
        report.add(
            "source-duplicate",
            "source_commits must not contain duplicates",
            f"{location}.source_commits",
            output_index=index,
            source_commits=output.source_commits,
        )
        valid = False
    if not unknown_sources:
        positions = tuple(source_positions[item] for item in output.source_commits)
        if positions != tuple(sorted(positions)):
            report.add(
                "source-order",
                "source_commits must retain source order",
                f"{location}.source_commits",
                output_index=index,
                source_commits=output.source_commits,
            )
            valid = False
    required_sources = 2 if output.operation == "INTEGRATE" else 1
    cardinality_valid = (
        len(output.source_commits) >= required_sources
        if output.operation == "INTEGRATE"
        else len(output.source_commits) == required_sources
    )
    if not cardinality_valid:
        report.add(
            "operation-source-cardinality",
            (
                "INTEGRATE must consume at least two source commits"
                if output.operation == "INTEGRATE"
                else f"{output.operation} must consume one source commit"
            ),
            f"{location}.source_commits",
            output_index=index,
            source_commits=output.source_commits,
        )
        valid = False

    unknown_units = tuple(
        unit_id for unit_id in output.source_unit_ids if unit_id not in unit_by_id
    )
    if unknown_units:
        report.add(
            "unit-unknown",
            "source_unit_ids contains an unknown unit",
            f"{location}.source_unit_ids",
            output_index=index,
            unit_ids=unknown_units,
        )
        valid = False
    if len(set(output.source_unit_ids)) != len(output.source_unit_ids):
        report.add(
            "unit-duplicate",
            "source_unit_ids must not contain duplicates",
            f"{location}.source_unit_ids",
            output_index=index,
            unit_ids=output.source_unit_ids,
        )
        valid = False
    if output.materialization == "RESOLVED" and not output.source_unit_ids:
        report.add(
            "resolved-units-empty",
            "RESOLVED must declare at least one source unit",
            f"{location}.source_unit_ids",
            output_index=index,
        )
        valid = False
    if unknown_sources or unknown_units or not sources:
        return False

    source_order = {
        source.commit_id: source_index for source_index, source in enumerate(sources)
    }
    selected_units = tuple(unit_by_id[item] for item in output.source_unit_ids)
    unlisted_units = tuple(
        unit for unit in selected_units if unit.source_commit not in source_order
    )
    if unlisted_units:
        report.add(
            "unit-source-unlisted",
            "source_unit_ids contains a unit from an unlisted source",
            f"{location}.source_unit_ids",
            output_index=index,
            source_commits=output.source_commits,
            units=unlisted_units,
        )
        valid = False
        return False
    selected_source_ids = {unit.source_commit for unit in selected_units}
    for source in sources[1:]:
        if source.units and source.commit_id not in selected_source_ids:
            report.add(
                "source-units-empty",
                f"lists source {source.commit_id} without any of its units",
                f"{location}.source_unit_ids",
                output_index=index,
                source_commits=(source.commit_id,),
            )
            valid = False
    selected_keys = tuple(
        (
            source_order[unit.source_commit],
            unit_positions[unit.source_commit][unit.unit_id],
        )
        for unit in selected_units
    )
    if selected_keys != tuple(sorted(selected_keys)):
        report.add(
            "unit-order",
            "source_unit_ids must retain source and unit order",
            f"{location}.source_unit_ids",
            output_index=index,
            units=selected_units,
        )
        valid = False

    target_valid = _lint_output_target(index, output, sources, selected_units, report)
    return valid and target_valid


def _lint_output_target(
    index: int,
    output: HistoryPlannedCommit,
    sources: tuple[HistoryCommitSnapshot, ...],
    selected_units: tuple[HistoryPatchUnit, ...],
    report: PlanDiagnosticCollector,
) -> bool:
    """Check target shape and report metadata/materialization restrictions."""
    valid = True
    location = f"plan.outputs[{index}]"
    target = sources[0]
    target_units = tuple(unit.unit_id for unit in target.units)
    if output.operation in {"KEEP", "REWORD", "REORDER"}:
        if output.source_unit_ids != target_units:
            operation_unit_message = (
                f"lists source {target.commit_id} without any of its units"
                if target.units and not output.source_unit_ids
                else f"{output.operation} must consume every target unit in order"
            )
            report.add(
                "operation-unit-shape",
                operation_unit_message,
                f"{location}.source_unit_ids",
                output_index=index,
                units=target.units,
            )
            valid = False
    elif output.operation == "SPLIT" and not output.source_unit_ids:
        report.add(
            "split-units-empty",
            "SPLIT must contain at least one source unit",
            f"{location}.source_unit_ids",
            output_index=index,
        )
        valid = False
    elif output.operation == "INTEGRATE":
        selected_target = tuple(
            unit.unit_id
            for unit in selected_units
            if unit.source_commit == target.commit_id
        )
        if selected_target != target_units:
            report.add(
                "operation-unit-shape",
                "INTEGRATE must consume every target unit in order",
                f"{location}.source_unit_ids",
                output_index=index,
                units=target.units,
            )
            valid = False
    if output.author != target.author:
        report.add(
            "target-author-changed",
            "author must preserve the target author",
            f"{location}.author",
            output_index=index,
            source_commits=(target.commit_id,),
        )
    if output.operation in {"KEEP", "REORDER"} and (
        output.message != target.message or output.encoding != target.encoding
    ):
        report.add(
            "operation-message-shape",
            "message or encoding changed without a REWORD, SPLIT, or INTEGRATE operation",
            location,
            output_index=index,
            source_commits=(target.commit_id,),
        )
    unsupported_sources = tuple(
        source for source in sources if source.unsupported_headers
    )
    if unsupported_sources:
        report.add(
            "source-headers-unsupported",
            "; ".join(
                f"source commit {source.commit_id} has unsupported header(s): "
                + ", ".join(source.unsupported_headers)
                for source in unsupported_sources
            ),
            location,
            output_index=index,
            source_commits=tuple(source.commit_id for source in unsupported_sources),
        )
    if output.materialization == "RESOLVED":
        unsupported_kind = tuple(
            unit
            for unit in selected_units
            if unit.kind in _UNSUPPORTED_RESOLUTION_KINDS
        )
        unsupported_coupling = tuple(
            unit
            for unit in selected_units
            if unit.unsupported_reason in _UNSUPPORTED_RESOLUTION_REASONS
        )
        if unsupported_kind:
            report.add(
                "resolution-unit-kind-unsupported",
                "RESOLVED output contains unsupported unit kind(s): "
                + ", ".join(dict.fromkeys(unit.kind for unit in unsupported_kind)),
                f"{location}.source_unit_ids",
                output_index=index,
                units=unsupported_kind,
                resolved_supported=False,
            )
        if unsupported_coupling:
            report.add(
                "resolution-coupling-unsupported",
                "RESOLVED output contains unsupported coupling(s): "
                + ", ".join(
                    dict.fromkeys(
                        unit.unsupported_reason or "unknown"
                        for unit in unsupported_coupling
                    )
                ),
                f"{location}.source_unit_ids",
                output_index=index,
                units=unsupported_coupling,
                resolved_supported=False,
            )

    return valid
