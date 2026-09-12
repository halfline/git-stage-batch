"""Enforce history-plan semantics independently of file loading and replay."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from .models import HistoryPlan, HistoryPlanOperation, HistorySnapshot
from .plan_dependencies import (
    PlanDependencyEvidenceError,
    iter_plan_dependency_crossings,
)
from .plan_inventory_validation import (
    validate_plan_partitions,
    validate_source_consumption,
    validate_relative_output_order,
)
from .plan_output_validation import validate_plan_output
from .plan_source_index import PlanSourceIndex


def validate_plan_semantics(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
    _invalid: Callable[[str], NoReturn],
) -> None:
    """Require each phase in order, stopping at the first failure."""
    if not plan.outputs:
        _invalid("plan.outputs must contain at least one output commit")
    pinned_commit_ids = {
        commit.commit_id for commit in snapshot.commits[: snapshot.movable_commit_start]
    }
    source_index = PlanSourceIndex.build(snapshot)
    expected_units = tuple(
        unit.unit_id for commit in snapshot.commits for unit in commit.units
    )
    unit_occurrences: dict[str, list[int]] = {unit_id: [] for unit_id in expected_units}
    target_occurrences: dict[str, list[tuple[int, HistoryPlanOperation]]] = {}
    secondary_occurrences: dict[str, list[int]] = {}
    source_mentions: dict[str, int] = {}
    output_target_positions: list[int] = []
    for index, output in enumerate(plan.outputs):
        sources = validate_plan_output(
            index,
            output,
            source_index,
            pinned_commit_ids,
            unit_occurrences,
            _invalid,
        )
        target_source = sources[0]
        target_occurrences.setdefault(target_source.commit_id, []).append(
            (index, output.operation)
        )
        output_target_positions.append(
            source_index.source_positions[target_source.commit_id]
        )
        for source in sources:
            source_mentions[source.commit_id] = (
                source_mentions.get(source.commit_id, 0) + 1
            )
        for secondary_source in sources[1:]:
            secondary_occurrences.setdefault(
                secondary_source.commit_id,
                [],
            ).append(index)

    partitioned_by_id = validate_plan_partitions(
        plan,
        source_index,
        expected_units,
        unit_occurrences,
        _invalid,
    )
    for unit_id in expected_units:
        occurrences = unit_occurrences[unit_id]
        if unit_id in partitioned_by_id:
            continue
        if len(occurrences) != 1:
            _invalid(
                "plan.outputs must assign every nonpartitioned source unit exactly once"
            )

    validate_source_consumption(
        snapshot,
        target_occurrences,
        secondary_occurrences,
        source_mentions,
        _invalid,
    )
    validate_relative_output_order(plan, output_target_positions, _invalid)
    try:
        for crossing in iter_plan_dependency_crossings(
            snapshot, plan, expected_units, partitioned_by_id
        ):
            _invalid(
                f"planned unit order crosses a {crossing.barrier} dependency between "
                f"{crossing.crossed_unit_id} and {crossing.dependency.unit_id}"
            )
    except PlanDependencyEvidenceError as error:
        _invalid(str(error))
