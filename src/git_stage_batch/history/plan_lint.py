"""Run ordered advisory validation phases over a frozen history plan."""

from __future__ import annotations

from .models import HistoryPartitionedUnit, HistoryPlan, HistorySnapshot
from .plan_dependencies import (
    PlanDependencyEvidenceError,
    iter_plan_dependency_crossings,
)
from .plan_diagnostics import HistoryPlanLint, PlanDiagnosticCollector
from .plan_inventory_lint import (
    lint_plan_partitions,
    lint_unit_conservation,
    lint_source_consumption,
    lint_relative_output_order,
    lint_movable_scope,
)
from .plan_output_lint import (
    lint_plan_output,
    _UNSUPPORTED_RESOLUTION_KINDS,
    _UNSUPPORTED_RESOLUTION_REASONS,
)
from .plan_source_index import PlanSourceIndex


def _lint_dependencies(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
    source_index: PlanSourceIndex,
    expected_units: tuple[str, ...],
    partitioned: dict[str, HistoryPartitionedUnit],
    report: PlanDiagnosticCollector,
) -> None:
    """Translate shared crossing results into advisory diagnostics."""
    unit_by_id = source_index.unit_by_id
    try:
        for crossing in iter_plan_dependency_crossings(
            snapshot, plan, expected_units, partitioned
        ):
            dependency = crossing.dependency
            moving_output = crossing.output_index
            barrier = crossing.barrier
            output = plan.outputs[moving_output]
            moving_unit = unit_by_id[dependency.unit_id]
            crossed_unit = unit_by_id[crossing.crossed_unit_id]
            output_units = tuple(unit_by_id[item] for item in output.source_unit_ids)
            resolution_supported = not any(
                unit.kind in _UNSUPPORTED_RESOLUTION_KINDS
                or unit.unsupported_reason in _UNSUPPORTED_RESOLUTION_REASONS
                for unit in output_units
            )
            report.add(
                "dependency-crossing-unknown"
                if barrier == "UNKNOWN"
                else "dependency-crossing-blocked",
                f"planned unit order crosses a {barrier} dependency",
                f"plan.outputs[{moving_output}].source_unit_ids",
                output_index=moving_output,
                units=(crossed_unit, moving_unit),
                barrier=barrier,
                barrier_unit_id=dependency.barrier_unit_id,
                exact_supported=False,
                resolved_supported=resolution_supported,
            )
    except PlanDependencyEvidenceError as error:
        report.add(
            error.code,
            str(error),
            "snapshot.dependency_graph.units",
            unit_ids=(error.unit_id,) if error.unit_id is not None else (),
        )
        report.skipped_checks.append("dependencies")


def lint_frozen_history_plan(
    snapshot: HistorySnapshot, plan: HistoryPlan
) -> HistoryPlanLint:
    """Run safe phases in order, preserving root findings and skip decisions."""
    report = PlanDiagnosticCollector(snapshot, plan)
    if not plan.outputs:
        report.add(
            "outputs-empty",
            "plan.outputs must contain at least one output commit",
            "plan.outputs",
        )
        report.skipped_checks.append("global")
        return report.finish()
    source_index = PlanSourceIndex.build(snapshot)
    outputs_valid = True
    for index, output in enumerate(plan.outputs):
        if not lint_plan_output(index, output, source_index, report):
            outputs_valid = False
    if not outputs_valid:
        report.skipped_checks.extend(("conservation", "relative-order", "dependencies"))
        return report.finish()
    expected_units = tuple(
        unit.unit_id for commit in snapshot.commits for unit in commit.units
    )
    occurrences: dict[str, list[int]] = {unit_id: [] for unit_id in expected_units}
    for output_index, output in enumerate(plan.outputs):
        for unit_id in output.source_unit_ids:
            occurrences[unit_id].append(output_index)
    partitioned, partition_inventory_valid = lint_plan_partitions(
        plan,
        source_index,
        expected_units,
        occurrences,
        report,
    )
    conservation_valid = lint_unit_conservation(
        source_index,
        occurrences,
        partitioned,
        partition_inventory_valid,
        report,
    )
    lint_source_consumption(snapshot, plan, report)
    lint_relative_output_order(plan, source_index, report)
    lint_movable_scope(snapshot, plan, source_index, report)
    if not conservation_valid:
        if "dependencies" not in report.skipped_checks:
            report.skipped_checks.append("dependencies")
        return report.finish()
    _lint_dependencies(
        snapshot, plan, source_index, expected_units, partitioned, report
    )
    return report.finish()
