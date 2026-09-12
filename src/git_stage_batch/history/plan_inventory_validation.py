"""Enforced partition, consumption, and relative output-order invariants."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from .models import (
    HistoryPlan,
    HistoryPlanOperation,
    HistorySnapshot,
    HistoryPartitionedUnit,
)
from .plan_source_index import PlanSourceIndex


def validate_plan_partitions(
    plan: HistoryPlan,
    source_index: PlanSourceIndex,
    expected_units: tuple[str, ...],
    unit_occurrences: dict[str, list[int]],
    _invalid: Callable[[str], NoReturn],
) -> dict[str, HistoryPartitionedUnit]:
    """Require exact partition inventories and permitted target operations."""
    expected_unit_set = set(expected_units)
    source_by_id = source_index.source_by_id
    partitioned_by_id: dict[str, HistoryPartitionedUnit] = {}
    partition_positions: list[int] = []
    expected_positions = {
        unit_id: position for position, unit_id in enumerate(expected_units)
    }
    for index, partition in enumerate(plan.partitioned_units):
        location = f"plan.partitioned_units[{index}]"
        if partition.unit_id in partitioned_by_id:
            _invalid(f"{location}.unit_id duplicates a partitioned unit")
        if partition.unit_id not in expected_unit_set:
            _invalid(f"{location}.unit_id names an unknown unit")
        if len(partition.output_indexes) < 2:
            _invalid(f"{location}.output_indexes must contain at least two outputs")
        if partition.output_indexes != tuple(sorted(set(partition.output_indexes))):
            _invalid(
                f"{location}.output_indexes must be unique and strictly increasing"
            )
        if partition.output_indexes[-1] >= len(plan.outputs):
            _invalid(f"{location}.output_indexes contains an unknown output")
        if tuple(unit_occurrences[partition.unit_id]) != partition.output_indexes:
            _invalid(f"{location}.output_indexes must exactly match the unit's outputs")
        if any(
            plan.outputs[output_index].materialization != "RESOLVED"
            for output_index in partition.output_indexes
        ):
            _invalid(f"{location} may appear only in RESOLVED outputs")
        partitioned_by_id[partition.unit_id] = partition
        partition_positions.append(expected_positions[partition.unit_id])
    if partition_positions != sorted(partition_positions):
        _invalid("plan.partitioned_units must retain source unit order")

    partitioned_unit_ids = set(partitioned_by_id)
    for index, output in enumerate(plan.outputs):
        if output.operation == "SPLIT":
            continue
        partition_checked_target_units = {
            unit.unit_id for unit in source_by_id[output.source_commits[0]].units
        }
        if partition_checked_target_units & partitioned_unit_ids:
            _invalid(
                f"plan.outputs[{index}].{output.operation} target units must "
                "not be partitioned"
            )

    return partitioned_by_id


def validate_source_consumption(
    snapshot: HistorySnapshot,
    target_occurrences: dict[str, list[tuple[int, HistoryPlanOperation]]],
    secondary_occurrences: dict[str, list[int]],
    source_mentions: dict[str, int],
    _invalid: Callable[[str], NoReturn],
) -> None:
    """Require valid donor, residual SPLIT, and empty-commit consumption."""
    source_commits = snapshot.commits
    for source in source_commits:
        source_id = source.commit_id
        targets = target_occurrences.get(source_id, [])
        secondary = secondary_occurrences.get(source_id, [])
        if targets and secondary:
            if any(operation != "SPLIT" for _output, operation in targets):
                _invalid(
                    f"source commit {source_id} may be both a secondary and a "
                    "target only through residual SPLIT outputs"
                )
            target_indexes = [output for output, _operation in targets]
            if max(secondary) >= min(target_indexes):
                _invalid(
                    f"source commit {source_id} secondary outputs must precede "
                    "its residual SPLIT outputs"
                )
            if len(set((*secondary, *target_indexes))) < 2:
                _invalid(
                    f"source commit {source_id} must have at least two "
                    "destinations when split across target and secondary outputs"
                )
        if not targets and not secondary:
            _invalid(f"source commit {source_id} is not consumed by the plan")
        if len(targets) > 1 and any(
            operation != "SPLIT" for _output, operation in targets
        ):
            _invalid(
                f"source commit {source_id} may target several outputs only "
                "through SPLIT"
            )
        if len(targets) == 1 and targets[0][1] == "SPLIT" and not secondary:
            _invalid(
                f"source commit {source_id} must produce at least two SPLIT outputs"
            )
        if not source.units and source_mentions.get(source_id, 0) != 1:
            _invalid(f"empty source commit {source_id} must be consumed exactly once")


def validate_relative_output_order(
    plan: HistoryPlan,
    output_target_positions: list[int],
    _invalid: Callable[[str], NoReturn],
) -> None:
    """Require explicit movement operations using one reverse scan."""
    moved_earlier_outputs: set[int] = set()
    suffix_minimum = output_target_positions[-1]
    for earlier_index in range(len(output_target_positions) - 2, -1, -1):
        earlier_position = output_target_positions[earlier_index]
        if earlier_position > suffix_minimum:
            moved_earlier_outputs.add(earlier_index)
            if plan.outputs[earlier_index].operation not in {"REORDER", "SPLIT"}:
                _invalid(
                    f"plan.outputs[{earlier_index}] must use REORDER or SPLIT "
                    "when moving before an earlier source"
                )
        suffix_minimum = min(suffix_minimum, earlier_position)
    for output_index, output in enumerate(plan.outputs):
        if output.operation == "REORDER" and output_index not in moved_earlier_outputs:
            _invalid(
                f"plan.outputs[{output_index}].REORDER does not move its source earlier"
            )
