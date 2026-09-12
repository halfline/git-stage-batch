"""Shared dependency evidence and crossing checks for history plans."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .models import (
    HistoryPartitionedUnit,
    HistoryPlan,
    HistorySnapshot,
    HistoryUnitDependency,
)


class PrefixMaximumIndex:
    """Find the first prefix position above a threshold in logarithmic time."""

    def __init__(self, values: tuple[int, ...]) -> None:
        size = 1
        while size < len(values):
            size *= 2
        self._size = size
        maxima = [-1] * (size * 2)
        maxima[size : size + len(values)] = values
        for index in range(size - 1, 0, -1):
            maxima[index] = max(maxima[index * 2], maxima[index * 2 + 1])
        self._maxima = maxima

    def first_above(self, end: int, threshold: int) -> int | None:
        """Return the first index below ``end`` whose value exceeds threshold."""

        def search(node: int, left: int, right: int) -> int | None:
            if left >= end or self._maxima[node] <= threshold:
                return None
            if right - left == 1:
                return left
            middle = (left + right) // 2
            found = search(node * 2, left, middle)
            if found is not None:
                return found
            return search(node * 2 + 1, middle, right)

        return search(1, 0, self._size)


def grouped_block_chain_can_defer_to_replay(
    dependency: HistoryUnitDependency,
    *,
    dependencies_by_unit: dict[str, HistoryUnitDependency],
    first_crossings: dict[str, str | None],
    desired_positions: dict[str, int],
    output_positions: dict[str, int],
) -> bool:
    """Return whether ordered blockers move together inside one output."""
    visited: set[str] = set()
    current = dependency
    while first_crossings[current.unit_id] is not None:
        if current.unit_id in visited:
            return False
        visited.add(current.unit_id)
        barrier_unit = current.barrier_unit_id
        if (
            current.barrier != "BLOCKED"
            or barrier_unit is None
            or barrier_unit not in output_positions
            or output_positions[barrier_unit] != output_positions[current.unit_id]
            or desired_positions[barrier_unit] >= desired_positions[current.unit_id]
        ):
            return False
        current = dependencies_by_unit[barrier_unit]
    return True


class PlanDependencyEvidenceError(ValueError):
    """Malformed frozen evidence, before any crossing can be reported."""

    def __init__(self, code: str, message: str, unit_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.unit_id = unit_id


@dataclass(frozen=True, slots=True)
class PlanDependencyCrossing:
    """One crossing requiring a resolved output rather than exact replay."""

    dependency: HistoryUnitDependency
    crossed_unit_id: str
    output_index: int
    barrier: str | None


def _require_dependency_evidence(
    dependency: HistoryUnitDependency,
    expected_units: tuple[str, ...],
) -> None:
    original_position = dependency.original_position
    if (
        original_position >= len(expected_units)
        or expected_units[original_position] != dependency.unit_id
        or dependency.earliest_position < 0
        or dependency.earliest_position > original_position
    ):
        raise PlanDependencyEvidenceError(
            "dependency-position-invalid",
            "snapshot dependency graph has inconsistent unit positions",
            dependency.unit_id,
        )
    expected_barrier = (
        expected_units[dependency.earliest_position - 1]
        if dependency.earliest_position > 0
        else None
    )
    if (
        dependency.barrier_unit_id != expected_barrier
        or (dependency.barrier is None) != (dependency.detail is None)
        or (expected_barrier is not None and dependency.barrier is None)
        or (expected_barrier is None and dependency.barrier == "BLOCKED")
    ):
        raise PlanDependencyEvidenceError(
            "dependency-barrier-invalid",
            "snapshot dependency graph has inconsistent barrier evidence",
            dependency.unit_id,
        )


def _first_dependency_crossings(
    snapshot: HistorySnapshot,
    expected_units: tuple[str, ...],
    partitioned: dict[str, HistoryPartitionedUnit],
    desired_positions: dict[str, int],
    output_positions: dict[str, int],
) -> dict[str, str | None]:
    """Use two prefix indexes to find the first crossed source unit."""
    nonpartitioned_index = PrefixMaximumIndex(
        tuple(
            desired_positions.get(unit_id, -1) if unit_id not in partitioned else -1
            for unit_id in expected_units
        )
    )
    partitioned_index = PrefixMaximumIndex(
        tuple(
            max(partitioned[unit_id].output_indexes) if unit_id in partitioned else -1
            for unit_id in expected_units
        )
    )
    first_crossings: dict[str, str | None] = {}
    for dependency in snapshot.dependencies:
        _require_dependency_evidence(dependency, expected_units)
        if dependency.unit_id in partitioned:
            first_crossings[dependency.unit_id] = None
            continue
        candidates = tuple(
            position
            for position in (
                nonpartitioned_index.first_above(
                    dependency.earliest_position,
                    desired_positions[dependency.unit_id],
                ),
                partitioned_index.first_above(
                    dependency.earliest_position,
                    output_positions[dependency.unit_id],
                ),
            )
            if position is not None
        )
        first_crossings[dependency.unit_id] = (
            expected_units[min(candidates)] if candidates else None
        )
    return first_crossings


def iter_plan_dependency_crossings(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
    expected_units: tuple[str, ...],
    partitioned: dict[str, HistoryPartitionedUnit],
) -> Iterator[PlanDependencyCrossing]:
    """Validate all evidence, then yield exact-replay violations in source order.

    Indexes contain unit/output metadata, never patch lines. Each source-prefix
    query remains logarithmic; consumers stream the findings without copying
    the dependency inventory or building a list of all crossed pairs.
    """
    if len(snapshot.dependencies) != len(expected_units):
        raise PlanDependencyEvidenceError(
            "dependency-inventory-invalid",
            "snapshot dependency graph does not cover every patch unit",
        )
    ordered_nonpartitioned_units = tuple(
        unit_id
        for output in plan.outputs
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned
    )
    desired_positions = {
        unit_id: position
        for position, unit_id in enumerate(ordered_nonpartitioned_units)
    }
    output_positions = {
        unit_id: output_index
        for output_index, output in enumerate(plan.outputs)
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned
    }
    dependencies_by_unit = {
        dependency.unit_id: dependency for dependency in snapshot.dependencies
    }
    first_crossings = _first_dependency_crossings(
        snapshot,
        expected_units,
        partitioned,
        desired_positions,
        output_positions,
    )
    for dependency in snapshot.dependencies:
        crossed_unit_id = first_crossings[dependency.unit_id]
        if crossed_unit_id is None:
            continue
        moving_output = output_positions[dependency.unit_id]
        if plan.outputs[moving_output].materialization == "RESOLVED":
            continue
        if (
            crossed_unit_id not in partitioned
            and grouped_block_chain_can_defer_to_replay(
                dependency,
                dependencies_by_unit=dependencies_by_unit,
                first_crossings=first_crossings,
                desired_positions=desired_positions,
                output_positions=output_positions,
            )
        ):
            continue
        yield PlanDependencyCrossing(
            dependency,
            crossed_unit_id,
            moving_output,
            dependency.barrier
            if crossed_unit_id == dependency.barrier_unit_id
            else "UNKNOWN",
        )
