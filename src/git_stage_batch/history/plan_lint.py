"""Fast advisory validation of a frozen history rewrite plan."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    HistoryPartitionedUnit,
    HistoryPatchUnit,
    HistoryPlan,
    HistorySnapshot,
    HistoryUnitDependency,
)


_UNSUPPORTED_RESOLUTION_KINDS = frozenset({"rename", "file-type", "gitlink"})
_UNSUPPORTED_RESOLUTION_REASONS = frozenset(
    {"rename-with-content", "file-type-with-content"}
)


@dataclass(frozen=True, slots=True)
class HistoryPlanDiagnostic:
    """One stable, machine-readable advisory plan finding."""

    code: str
    message: str
    location: str
    output_index: int | None = None
    output_subject: str | None = None
    operation: str | None = None
    materialization: str | None = None
    source_commits: tuple[str, ...] = ()
    unit_ids: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    unit_kinds: tuple[str, ...] = ()
    barrier: str | None = None
    barrier_unit_id: str | None = None
    exact_supported: bool | None = None
    resolved_supported: bool | None = None


@dataclass(frozen=True, slots=True)
class HistoryPlanLint:
    """Advisory findings derived only from the persisted document."""

    snapshot: HistorySnapshot
    plan: HistoryPlan
    diagnostics: tuple[HistoryPlanDiagnostic, ...]
    skipped_checks: tuple[str, ...]

    @property
    def valid(self) -> bool:
        """Return whether the advisory pass found no plan errors."""
        return not self.diagnostics


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


def lint_frozen_history_plan(
    snapshot: HistorySnapshot,
    plan: HistoryPlan,
) -> HistoryPlanLint:
    """Check plan shape and frozen-snapshot semantics without Git or replay.

    Checks are deliberately phased.  Later global checks are skipped when an
    output has invalid references or ordering, preventing one root mistake
    from producing a page of misleading conservation and dependency errors.
    """
    diagnostics: list[HistoryPlanDiagnostic] = []
    skipped: list[str] = []
    source_by_id = {commit.commit_id: commit for commit in snapshot.commits}
    source_positions = {
        commit.commit_id: index for index, commit in enumerate(snapshot.commits)
    }
    unit_by_id = {
        unit.unit_id: unit for commit in snapshot.commits for unit in commit.units
    }
    unit_positions = {
        commit.commit_id: {
            unit.unit_id: index for index, unit in enumerate(commit.units)
        }
        for commit in snapshot.commits
    }
    output_valid: list[bool] = [True] * len(plan.outputs)

    def add(
        code: str,
        message: str,
        location: str,
        *,
        output_index: int | None = None,
        source_commits: tuple[str, ...] = (),
        units: tuple[HistoryPatchUnit, ...] = (),
        unit_ids: tuple[str, ...] = (),
        barrier: str | None = None,
        barrier_unit_id: str | None = None,
        exact_supported: bool | None = None,
        resolved_supported: bool | None = None,
    ) -> None:
        output = plan.outputs[output_index] if output_index is not None else None
        diagnostics.append(
            HistoryPlanDiagnostic(
                code=code,
                message=message,
                location=location,
                output_index=output_index,
                output_subject=(
                    output.message.splitlines()[0] if output and output.message else ""
                )
                if output is not None
                else None,
                operation=output.operation if output is not None else None,
                materialization=(
                    output.materialization if output is not None else None
                ),
                source_commits=source_commits,
                unit_ids=unit_ids or tuple(unit.unit_id for unit in units),
                paths=tuple(dict.fromkeys(unit.path for unit in units)),
                unit_kinds=tuple(dict.fromkeys(unit.kind for unit in units)),
                barrier=barrier,
                barrier_unit_id=barrier_unit_id,
                exact_supported=exact_supported,
                resolved_supported=resolved_supported,
            )
        )

    if not plan.outputs:
        add(
            "outputs-empty",
            "plan.outputs must contain at least one output commit",
            "plan.outputs",
        )
        return HistoryPlanLint(snapshot, plan, tuple(diagnostics), ("global",))

    for index, output in enumerate(plan.outputs):
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
            add(
                "sources-empty",
                "source_commits must not be empty",
                f"{location}.source_commits",
                output_index=index,
            )
            output_valid[index] = False
        if unknown_sources:
            add(
                "source-unknown",
                "source_commits contains an unknown commit",
                f"{location}.source_commits",
                output_index=index,
                source_commits=unknown_sources,
            )
            output_valid[index] = False
        if len(set(output.source_commits)) != len(output.source_commits):
            add(
                "source-duplicate",
                "source_commits must not contain duplicates",
                f"{location}.source_commits",
                output_index=index,
                source_commits=output.source_commits,
            )
            output_valid[index] = False
        if not unknown_sources:
            positions = tuple(source_positions[item] for item in output.source_commits)
            if positions != tuple(sorted(positions)):
                add(
                    "source-order",
                    "source_commits must retain source order",
                    f"{location}.source_commits",
                    output_index=index,
                    source_commits=output.source_commits,
                )
                output_valid[index] = False
        required_sources = 2 if output.operation == "INTEGRATE" else 1
        cardinality_valid = (
            len(output.source_commits) >= required_sources
            if output.operation == "INTEGRATE"
            else len(output.source_commits) == required_sources
        )
        if not cardinality_valid:
            add(
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
            output_valid[index] = False

        unknown_units = tuple(
            unit_id for unit_id in output.source_unit_ids if unit_id not in unit_by_id
        )
        if unknown_units:
            add(
                "unit-unknown",
                "source_unit_ids contains an unknown unit",
                f"{location}.source_unit_ids",
                output_index=index,
                unit_ids=unknown_units,
            )
            output_valid[index] = False
        if len(set(output.source_unit_ids)) != len(output.source_unit_ids):
            add(
                "unit-duplicate",
                "source_unit_ids must not contain duplicates",
                f"{location}.source_unit_ids",
                output_index=index,
                unit_ids=output.source_unit_ids,
            )
            output_valid[index] = False
        if output.materialization == "RESOLVED" and not output.source_unit_ids:
            add(
                "resolved-units-empty",
                "RESOLVED must declare at least one source unit",
                f"{location}.source_unit_ids",
                output_index=index,
            )
            output_valid[index] = False
        if unknown_sources or unknown_units or not sources:
            continue

        source_order = {
            source.commit_id: source_index
            for source_index, source in enumerate(sources)
        }
        selected_units = tuple(unit_by_id[item] for item in output.source_unit_ids)
        unlisted_units = tuple(
            unit for unit in selected_units if unit.source_commit not in source_order
        )
        if unlisted_units:
            add(
                "unit-source-unlisted",
                "source_unit_ids contains a unit from an unlisted source",
                f"{location}.source_unit_ids",
                output_index=index,
                source_commits=output.source_commits,
                units=unlisted_units,
            )
            output_valid[index] = False
            continue
        selected_by_source = {
            source.commit_id: tuple(
                unit for unit in selected_units if unit.source_commit == source.commit_id
            )
            for source in sources
        }
        for source in sources[1:]:
            if source.units and not selected_by_source[source.commit_id]:
                add(
                    "source-units-empty",
                    f"lists source {source.commit_id} without any of its units",
                    f"{location}.source_unit_ids",
                    output_index=index,
                    source_commits=(source.commit_id,),
                )
                output_valid[index] = False
        selected_keys = tuple(
            (source_order[unit.source_commit], unit_positions[unit.source_commit][unit.unit_id])
            for unit in selected_units
        )
        if selected_keys != tuple(sorted(selected_keys)):
            add(
                "unit-order",
                "source_unit_ids must retain source and unit order",
                f"{location}.source_unit_ids",
                output_index=index,
                units=selected_units,
            )
            output_valid[index] = False

        target = sources[0]
        target_units = tuple(unit.unit_id for unit in target.units)
        if output.operation in {"KEEP", "REWORD", "REORDER"}:
            if output.source_unit_ids != target_units:
                operation_unit_message = (
                    f"lists source {target.commit_id} without any of its units"
                    if target.units and not output.source_unit_ids
                    else f"{output.operation} must consume every target unit in order"
                )
                add(
                    "operation-unit-shape",
                    operation_unit_message,
                    f"{location}.source_unit_ids",
                    output_index=index,
                    units=target.units,
                )
                output_valid[index] = False
        elif output.operation == "SPLIT" and not output.source_unit_ids:
            add(
                "split-units-empty",
                "SPLIT must contain at least one source unit",
                f"{location}.source_unit_ids",
                output_index=index,
            )
            output_valid[index] = False
        elif output.operation == "INTEGRATE":
            selected_target = tuple(
                unit.unit_id for unit in selected_units if unit.source_commit == target.commit_id
            )
            if selected_target != target_units:
                add(
                    "operation-unit-shape",
                    "INTEGRATE must consume every target unit in order",
                    f"{location}.source_unit_ids",
                    output_index=index,
                    units=target.units,
                )
                output_valid[index] = False
        if output.author != target.author:
            add(
                "target-author-changed",
                "author must preserve the target author",
                f"{location}.author",
                output_index=index,
                source_commits=(target.commit_id,),
            )
        if output.operation in {"KEEP", "REORDER"} and (
            output.message != target.message or output.encoding != target.encoding
        ):
            add(
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
            add(
                "source-headers-unsupported",
                "; ".join(
                    f"source commit {source.commit_id} has unsupported header(s): "
                    + ", ".join(source.unsupported_headers)
                    for source in unsupported_sources
                ),
                location,
                output_index=index,
                source_commits=tuple(
                    source.commit_id for source in unsupported_sources
                ),
            )
        if output.materialization == "RESOLVED":
            unsupported_kind = tuple(
                unit for unit in selected_units if unit.kind in _UNSUPPORTED_RESOLUTION_KINDS
            )
            unsupported_coupling = tuple(
                unit
                for unit in selected_units
                if unit.unsupported_reason in _UNSUPPORTED_RESOLUTION_REASONS
            )
            if unsupported_kind:
                add(
                    "resolution-unit-kind-unsupported",
                    "RESOLVED output contains unsupported unit kind(s): "
                    + ", ".join(dict.fromkeys(unit.kind for unit in unsupported_kind)),
                    f"{location}.source_unit_ids",
                    output_index=index,
                    units=unsupported_kind,
                    resolved_supported=False,
                )
            if unsupported_coupling:
                add(
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

    if not all(output_valid):
        skipped.extend(("conservation", "relative-order", "dependencies"))
        return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))

    expected_units = tuple(
        unit.unit_id for commit in snapshot.commits for unit in commit.units
    )
    occurrences: dict[str, list[int]] = {unit_id: [] for unit_id in expected_units}
    for output_index, output in enumerate(plan.outputs):
        for unit_id in output.source_unit_ids:
            occurrences[unit_id].append(output_index)
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
            add(
                "partition-duplicate",
                "unit_id duplicates a partitioned unit",
                f"{location}.unit_id",
                unit_ids=(partition.unit_id,),
            )
            partition_inventory_valid = False
            continue
        if partition.unit_id not in expected_unit_set:
            add(
                "partition-unit-unknown",
                "unit_id names an unknown unit",
                f"{location}.unit_id",
                unit_ids=(partition.unit_id,),
            )
            partition_inventory_valid = False
            continue
        if len(partition.output_indexes) < 2:
            add(
                "partition-destinations",
                "output_indexes must contain at least two outputs",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        if partition.output_indexes != tuple(sorted(set(partition.output_indexes))):
            add(
                "partition-output-order",
                "output_indexes must be unique and strictly increasing",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        if partition.output_indexes and partition.output_indexes[-1] >= len(plan.outputs):
            add(
                "partition-output-unknown",
                "output_indexes contains an unknown output",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        elif tuple(occurrences[partition.unit_id]) != partition.output_indexes:
            add(
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
            add(
                "partition-materialization",
                "a partitioned unit may appear only in RESOLVED outputs",
                f"{location}.output_indexes",
                units=(unit_by_id[partition.unit_id],),
            )
            partition_inventory_valid = False
        partitioned[partition.unit_id] = partition
        partition_positions.append(expected_positions[partition.unit_id])

    if partition_positions != sorted(partition_positions):
        add(
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
                add(
                    "partitioned-target-operation",
                    f"{output.operation} target units must not be partitioned",
                    f"plan.outputs[{output_index}].source_unit_ids",
                    output_index=output_index,
                    units=partitioned_target_units,
                )
                partition_inventory_valid = False

    conservation_valid = partition_inventory_valid
    if not partition_inventory_valid:
        skipped.extend(("conservation", "dependencies"))
    else:
        for unit_id, indexes in occurrences.items():
            declared_partition = partitioned.get(unit_id)
            if declared_partition is None and len(indexes) != 1:
                conservation_valid = False
                unit = unit_by_id[unit_id]
                add(
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
                add(
                    "partition-inventory-mismatch",
                    "partition output_indexes must exactly match the unit's outputs",
                    "plan.partitioned_units",
                    units=(unit,),
                )

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
            add(
                "source-unconsumed",
                "source commit is not consumed by the plan",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if targets and secondary:
            target_indexes = [index for index, _operation in targets]
            if any(operation != "SPLIT" for _index, operation in targets):
                add(
                    "source-target-secondary-shape",
                    "a source may be secondary and target only through residual SPLIT outputs",
                    "plan.outputs",
                    source_commits=(source.commit_id,),
                )
            elif max(secondary) >= min(target_indexes):
                add(
                    "source-target-secondary-order",
                    "secondary outputs must precede residual SPLIT outputs",
                    "plan.outputs",
                    source_commits=(source.commit_id,),
                )
        if len(targets) > 1 and any(
            operation != "SPLIT" for _index, operation in targets
        ):
            add(
                "source-multiple-targets",
                "a source may target several outputs only through SPLIT",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if len(targets) == 1 and targets[0][1] == "SPLIT" and not secondary:
            add(
                "split-destinations",
                "a SPLIT source must produce at least two outputs",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )
        if not source.units and source_mentions.get(source.commit_id, 0) != 1:
            add(
                "empty-source-conservation",
                "empty source commit must be consumed exactly once",
                "plan.outputs",
                source_commits=(source.commit_id,),
            )

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
            add(
                "moved-output-operation",
                "an output moving before an earlier source must use REORDER or SPLIT",
                f"plan.outputs[{index}].operation",
                output_index=index,
                source_commits=output.source_commits,
            )
        if output.operation == "REORDER" and not moved_earlier[index]:
            add(
                "reorder-without-movement",
                "REORDER does not move its source earlier",
                f"plan.outputs[{index}].operation",
                output_index=index,
                source_commits=output.source_commits,
            )

    if not conservation_valid:
        if "dependencies" not in skipped:
            skipped.append("dependencies")
        return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))

    partitioned_unit_ids = set(partitioned)
    ordered_nonpartitioned_units = tuple(
        unit_id
        for output in plan.outputs
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned_unit_ids
    )
    desired_positions = {
        unit_id: position
        for position, unit_id in enumerate(ordered_nonpartitioned_units)
    }
    output_positions = {
        unit_id: output_index
        for output_index, output in enumerate(plan.outputs)
        for unit_id in output.source_unit_ids
        if unit_id not in partitioned_unit_ids
    }
    if len(snapshot.dependencies) != len(expected_units):
        add(
            "dependency-inventory-invalid",
            "snapshot dependency graph does not cover every patch unit",
            "snapshot.dependency_graph.units",
        )
        skipped.append("dependencies")
        return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))
    dependencies_by_unit = {
        dependency.unit_id: dependency for dependency in snapshot.dependencies
    }
    nonpartitioned_index = PrefixMaximumIndex(
        tuple(
            desired_positions.get(unit_id, -1)
            if unit_id not in partitioned_unit_ids
            else -1
            for unit_id in expected_units
        )
    )
    partitioned_index = PrefixMaximumIndex(
        tuple(
            max(partitioned[unit_id].output_indexes)
            if unit_id in partitioned_unit_ids
            else -1
            for unit_id in expected_units
        )
    )
    first_crossings: dict[str, str | None] = {}
    for dependency in snapshot.dependencies:
        original_position = dependency.original_position
        if (
            original_position >= len(expected_units)
            or expected_units[original_position] != dependency.unit_id
            or dependency.earliest_position < 0
            or dependency.earliest_position > original_position
        ):
            add(
                "dependency-position-invalid",
                "snapshot dependency graph has inconsistent unit positions",
                "snapshot.dependency_graph.units",
                unit_ids=(dependency.unit_id,),
            )
            skipped.append("dependencies")
            return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))
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
            add(
                "dependency-barrier-invalid",
                "snapshot dependency graph has inconsistent barrier evidence",
                "snapshot.dependency_graph.units",
                unit_ids=(dependency.unit_id,),
            )
            skipped.append("dependencies")
            return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))
        if dependency.unit_id in partitioned_unit_ids:
            first_crossings[dependency.unit_id] = None
            continue
        moving_position = desired_positions[dependency.unit_id]
        moving_output = output_positions[dependency.unit_id]
        candidates = tuple(
            position
            for position in (
                nonpartitioned_index.first_above(
                    dependency.earliest_position,
                    moving_position,
                ),
                partitioned_index.first_above(
                    dependency.earliest_position,
                    moving_output,
                ),
            )
            if position is not None
        )
        first_crossings[dependency.unit_id] = (
            expected_units[min(candidates)] if candidates else None
        )

    for dependency in snapshot.dependencies:
        crossed_unit_id = first_crossings[dependency.unit_id]
        if crossed_unit_id is None:
            continue
        moving_output = output_positions[dependency.unit_id]
        output = plan.outputs[moving_output]
        if output.materialization == "RESOLVED":
            continue
        if crossed_unit_id not in partitioned_unit_ids and (
            grouped_block_chain_can_defer_to_replay(
                dependency,
                dependencies_by_unit=dependencies_by_unit,
                first_crossings=first_crossings,
                desired_positions=desired_positions,
                output_positions=output_positions,
            )
        ):
            continue
        barrier = (
            dependency.barrier
            if crossed_unit_id == dependency.barrier_unit_id
            else "UNKNOWN"
        )
        moving_unit = unit_by_id[dependency.unit_id]
        crossed_unit = unit_by_id[crossed_unit_id]
        output_units = tuple(unit_by_id[item] for item in output.source_unit_ids)
        resolution_supported = not any(
            unit.kind in _UNSUPPORTED_RESOLUTION_KINDS
            or unit.unsupported_reason in _UNSUPPORTED_RESOLUTION_REASONS
            for unit in output_units
        )
        add(
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
    return HistoryPlanLint(snapshot, plan, tuple(diagnostics), tuple(skipped))
