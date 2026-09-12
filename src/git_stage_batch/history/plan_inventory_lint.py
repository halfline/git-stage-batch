"""Advisory partition, conservation, source-consumption, and ordering checks."""

from __future__ import annotations

from .models import HistoryPartitionedUnit, HistoryPlan, HistorySnapshot
from .plan_diagnostics import PlanDiagnosticCollector
from .plan_source_index import PlanSourceIndex


def lint_plan_partitions(
    plan: HistoryPlan,
    source_index: PlanSourceIndex,
    expected_units: tuple[str, ...],
    occurrences: dict[str, list[int]],
    report: PlanDiagnosticCollector,
) -> tuple[dict[str, HistoryPartitionedUnit], bool]:
    """Validate partition declarations before checking unit conservation."""
    source_by_id = source_index.source_by_id
    unit_by_id = source_index.unit_by_id
    partitioned: dict[str, HistoryPartitionedUnit] = {}
    partition_inventory_valid = True
    expected_unit_set = set(expected_units)
    expected_positions = {
        unit_id: position for position, unit_id in enumerate(expected_units)
    }
    partition_positions: list[int] = []
    for index, partition in enumerate(plan.partitioned_units):
        location = f"plan.partitioned_units[{index}]"
        if partition.unit_id in partitioned:
            report.add(
                "partition-duplicate",
                "unit_id duplicates a partitioned unit",
                f"{location}.unit_id",
                unit_ids=(partition.unit_id,),
            )
            partition_inventory_valid = False
            continue
        if partition.unit_id not in expected_unit_set:
            report.add(
                "partition-unit-unknown",
                "unit_id names an unknown unit",
                f"{location}.unit_id",
                unit_ids=(partition.unit_id,),
            )
            partition_inventory_valid = False
            continue
        if len(partition.output_indexes) < 2:
            report.add(
                "partition-destinations",
                "output_indexes must contain at least two outputs",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        if partition.output_indexes != tuple(sorted(set(partition.output_indexes))):
            report.add(
                "partition-output-order",
                "output_indexes must be unique and strictly increasing",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        if partition.output_indexes and partition.output_indexes[-1] >= len(
            plan.outputs
        ):
            report.add(
                "partition-output-unknown",
                "output_indexes contains an unknown output",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        elif tuple(occurrences[partition.unit_id]) != partition.output_indexes:
            report.add(
                "partition-inventory-mismatch",
                "partition output_indexes must exactly match the unit's outputs",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        elif any(
            plan.outputs[output_index].materialization != "RESOLVED"
            for output_index in partition.output_indexes
        ):
            report.add(
                "partition-materialization",
                "a partitioned unit may appear only in RESOLVED outputs",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        partitioned[partition.unit_id] = partition
        partition_positions.append(expected_positions[partition.unit_id])

    if partition_positions != sorted(partition_positions):
        report.add(
            "partition-source-order",
            "partitioned_units must retain source unit order",
            "plan.partitioned_units",
        )
        partition_inventory_valid = False

    if partition_inventory_valid:
        partitioned_unit_ids = set(partitioned)
        for output_index, output in enumerate(plan.outputs):
            if output.operation == "SPLIT":
                continue
            partitioned_target_units = tuple(
                unit
                for unit in source_by_id[output.source_commits[0]].units
                if unit.unit_id in partitioned_unit_ids
            )
            if partitioned_target_units:
                report.add(
                    "partitioned-target-operation",
                    f"{output.operation} target units must not be partitioned",
                    f"plan.outputs[{output_index}].source_unit_ids",
                    output_index=output_index,
                    units=partitioned_target_units,
                )
                partition_inventory_valid = False

    return partitioned, partition_inventory_valid


def lint_unit_conservation(
    source_index: PlanSourceIndex,
    occurrences: dict[str, list[int]],
    partitioned: dict[str, HistoryPartitionedUnit],
    partition_inventory_valid: bool,
    report: PlanDiagnosticCollector,
) -> bool:
    """Check unit consumption only when partition references are trustworthy."""
    unit_by_id = source_index.unit_by_id
    conservation_valid = partition_inventory_valid
    if not partition_inventory_valid:
        report.skipped_checks.extend(("conservation", "dependencies"))
    else:
        for unit_id, indexes in occurrences.items():
            declared_partition = partitioned.get(unit_id)
            if declared_partition is None and len(indexes) != 1:
                conservation_valid = False
                unit = unit_by_id[unit_id]
                report.add(
                    "unit-conservation",
                    "nonpartitioned source unit must be assigned exactly once",
                    "plan.outputs",
                    units=(unit,),
                )
            elif (
                declared_partition is not None
                and tuple(indexes) != declared_partition.output_indexes
            ):
                conservation_valid = False
                unit = unit_by_id[unit_id]
                report.add(
                    "partition-inventory-mismatch",
                    "partition output_indexes must exactly match the unit's outputs",
                    "plan.partitioned_units",
                    units=(unit,),
                )

    return conservation_valid


def lint_source_consumption(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
    report: PlanDiagnosticCollector,
) -> None:
    """Check target, donor, residual SPLIT, and empty-commit consumption."""
    target_occurrences: dict[str, list[tuple[int, str]]] = {}
    secondary_occurrences: dict[str, list[int]] = {}
    source_mentions: dict[str, int] = {}
    for output_index, output in enumerate(plan.outputs):
        target_id = output.source_commits[0]
        target_occurrences.setdefault(target_id, []).append(
            (output_index, output.operation)
        )
        for source_id in output.source_commits:
            source_mentions[source_id] = source_mentions.get(source_id, 0) + 1
        for source_id in output.source_commits[1:]:
            secondary_occurrences.setdefault(source_id, []).append(output_index)
    for source in snapshot.commits:
        targets = target_occurrences.get(source.commit_id, [])
        secondary = secondary_occurrences.get(source.commit_id, [])
        if not targets and not secondary:
            report.add(
                "source-unconsumed",
                "source commit is not consumed by the plan",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if targets and secondary:
            target_indexes = [index for index, _operation in targets]
            if any(operation != "SPLIT" for _index, operation in targets):
                report.add(
                    "source-target-secondary-shape",
                    "a source may be secondary and target only through residual SPLIT outputs",
                    "plan.outputs",
                    source_commits=(source.commit_id,),
                )
            elif max(secondary) >= min(target_indexes):
                report.add(
                    "source-target-secondary-order",
                    "secondary outputs must precede residual SPLIT outputs",
                    "plan.outputs",
                    source_commits=(source.commit_id,),
                )
        if len(targets) > 1 and any(
            operation != "SPLIT" for _index, operation in targets
        ):
            report.add(
                "source-multiple-targets",
                "a source may target several outputs only through SPLIT",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if len(targets) == 1 and targets[0][1] == "SPLIT" and not secondary:
            report.add(
                "split-destinations",
                "a SPLIT source must produce at least two outputs",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if not source.units and source_mentions.get(source.commit_id, 0) != 1:
            report.add(
                "empty-source-conservation",
                "empty source commit must be consumed exactly once",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )


def lint_relative_output_order(
    plan: HistoryPlan,
    source_index: PlanSourceIndex,
    report: PlanDiagnosticCollector,
) -> None:
    """Find outputs moving earlier with one reverse scan of target positions."""
    source_positions = source_index.source_positions
    target_positions = [
        source_positions[output.source_commits[0]] for output in plan.outputs
    ]
    suffix_min = target_positions[-1]
    moved_earlier = [False] * len(plan.outputs)
    for index in range(len(plan.outputs) - 2, -1, -1):
        moved_earlier[index] = target_positions[index] > suffix_min
        suffix_min = min(suffix_min, target_positions[index])
    for index, output in enumerate(plan.outputs):
        if moved_earlier[index] and output.operation not in {"REORDER", "SPLIT"}:
            report.add(
                "moved-output-operation",
                "an output moving before an earlier source must use REORDER or SPLIT",
                f"plan.outputs[{index}].operation",
                output_index=index,
                source_commits=output.source_commits,
            )
        if output.operation == "REORDER" and not moved_earlier[index]:
            report.add(
                "reorder-without-movement",
                "REORDER does not move its source earlier",
                f"plan.outputs[{index}].operation",
                output_index=index,
                source_commits=output.source_commits,
            )


def lint_movable_scope(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
    source_index: PlanSourceIndex,
    report: PlanDiagnosticCollector,
) -> None:
    """Keep pinned sources out of donor, SPLIT, and REORDER positions."""
    source_positions = source_index.source_positions
    pinned_commit_ids: set[str] = set()
    if (
        snapshot.movable_base != snapshot.base_commit
        and snapshot.movable_base in source_positions
    ):
        boundary_position = source_positions[snapshot.movable_base]
        pinned_commit_ids = {
            commit.commit_id for commit in snapshot.commits[: boundary_position + 1]
        }
    for index, output in enumerate(plan.outputs):
        target_id = output.source_commits[0]
        if target_id in pinned_commit_ids and output.operation in {"SPLIT", "REORDER"}:
            report.add(
                "movable-scope-violation",
                "a pinned source commit outside the movable scope may not be "
                "split or reordered",
                f"plan.outputs[{index}].operation",
                output_index=index,
                source_commits=(target_id,),
            )
        pinned_donors = tuple(
            source_id
            for source_id in output.source_commits[1:]
            if source_id in pinned_commit_ids
        )
        if pinned_donors:
            report.add(
                "movable-scope-violation",
                "a pinned source commit outside the movable scope may not donate "
                "units; it may only receive units through INTEGRATE",
                f"plan.outputs[{index}].source_commits",
                output_index=index,
                source_commits=pinned_donors,
            )
