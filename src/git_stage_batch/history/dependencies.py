"""Per-unit partial-order evidence for history refinement."""

from __future__ import annotations

from dataclasses import replace

from ..fixup.commutation import (
    PatchApplicationResult,
    apply_patch_to_tree_result,
    load_tree_diff_as_buffer,
)
from ..utils.git_object_io import temporary_git_object_environment
from .models import (
    HistoryDependencyBarrier,
    HistorySnapshot,
    HistoryUnitDependency,
)
from .unit_replay import (
    HistoryReplayUnit,
    acquire_history_replay_units,
    apply_history_replay_unit,
)


def _dependency_for_replayable_unit(
    units: tuple[HistoryReplayUnit, ...],
    prefix_trees: tuple[str, ...],
    position: int,
    *,
    env: dict[str, str],
    replay_cache: dict[tuple[str, str], PatchApplicationResult] | None = None,
) -> HistoryUnitDependency:
    order = list(units[: position + 1])
    trees = list(prefix_trees[: position + 2])
    moving = units[position]
    current_position = position
    barrier_unit_id: str | None = None
    barrier: HistoryDependencyBarrier | None = None
    detail: str | None = None

    while current_position > 0:
        disjoint_position = _commute_across_disjoint_run(
            order,
            trees,
            moving,
            current_position,
            env=env,
            replay_cache=replay_cache,
        )
        if disjoint_position is not None:
            current_position = disjoint_position
            continue

        crossed = order[current_position - 1]
        moving_first = apply_history_replay_unit(
            trees[current_position - 1],
            moving,
            env=env,
            replay_cache=replay_cache,
        )
        if moving_first.status != "APPLIED" or moving_first.tree is None:
            barrier_unit_id = crossed.snapshot.unit_id
            barrier = "BLOCKED" if moving_first.status == "BLOCKED" else "UNKNOWN"
            detail = moving_first.detail
            break
        crossed_second = apply_history_replay_unit(
            moving_first.tree,
            crossed,
            env=env,
            replay_cache=replay_cache,
        )
        if crossed_second.status != "APPLIED" or crossed_second.tree is None:
            barrier_unit_id = crossed.snapshot.unit_id
            barrier = "BLOCKED" if crossed_second.status == "BLOCKED" else "UNKNOWN"
            detail = crossed_second.detail
            break
        if crossed_second.tree != trees[current_position + 1]:
            barrier_unit_id = crossed.snapshot.unit_id
            barrier = "BLOCKED"
            detail = "reversed-unit-order-produced-a-different-tree"
            break

        order[current_position - 1], order[current_position] = (
            order[current_position],
            order[current_position - 1],
        )
        trees[current_position] = moving_first.tree
        current_position -= 1

    return HistoryUnitDependency(
        unit_id=moving.snapshot.unit_id,
        original_position=position,
        earliest_position=current_position,
        barrier_unit_id=barrier_unit_id,
        barrier=barrier,
        detail=detail,
    )


def _commute_across_disjoint_run(
    order: list[HistoryReplayUnit],
    trees: list[str],
    moving: HistoryReplayUnit,
    current_position: int,
    *,
    env: dict[str, str],
    replay_cache: dict[tuple[str, str], PatchApplicationResult] | None = None,
) -> int | None:
    """Prove one maximal different-path run with a single block replay."""
    earliest_position = current_position
    while earliest_position > 0:
        crossed = order[earliest_position - 1]
        if (
            not crossed.individually_replayable
            or crossed.snapshot.path == moving.snapshot.path
        ):
            break
        earliest_position -= 1
    if earliest_position == current_position:
        return None

    moving_first = apply_history_replay_unit(
        trees[earliest_position],
        moving,
        env=env,
        replay_cache=replay_cache,
    )
    if moving_first.status != "APPLIED" or moving_first.tree is None:
        return None
    with load_tree_diff_as_buffer(
        trees[earliest_position],
        trees[current_position],
        env=env,
    ) as crossed_patch:
        replayed = apply_patch_to_tree_result(
            moving_first.tree,
            crossed_patch.byte_chunks(),
            three_way=True,
            env=env,
        )
    if replayed.status != "APPLIED" or replayed.tree != trees[current_position + 1]:
        return None

    order.insert(earliest_position, order.pop(current_position))
    trees[earliest_position + 1] = moving_first.tree
    return earliest_position


def _record_replayable_segment(
    dependencies: list[HistoryUnitDependency | None],
    expected_unit_ids: tuple[str, ...],
    segment_start: int,
    segment_units: tuple[HistoryReplayUnit, ...],
    segment_trees: tuple[str, ...],
    boundary_detail: str | None,
    *,
    env: dict[str, str],
    replay_cache: dict[tuple[str, str], PatchApplicationResult] | None = None,
) -> None:
    for local_position in range(len(segment_units)):
        dependency = _dependency_for_replayable_unit(
            segment_units,
            segment_trees,
            local_position,
            env=env,
            replay_cache=replay_cache,
        )
        original_position = segment_start + dependency.original_position
        earliest_position = segment_start + dependency.earliest_position
        if dependency.earliest_position == 0 and boundary_detail is not None:
            dependency = replace(
                dependency,
                barrier_unit_id=(
                    expected_unit_ids[segment_start - 1]
                    if segment_start > 0
                    else None
                ),
                barrier="UNKNOWN",
                detail=boundary_detail,
            )
        dependencies[original_position] = replace(
            dependency,
            original_position=original_position,
            earliest_position=earliest_position,
        )


def _unknown_dependency(
    unit_id: str,
    position: int,
    barrier_unit_id: str | None,
    detail: str,
) -> HistoryUnitDependency:
    return HistoryUnitDependency(
        unit_id=unit_id,
        original_position=position,
        earliest_position=position,
        barrier_unit_id=barrier_unit_id,
        barrier="UNKNOWN",
        detail=detail,
    )


def _pinned_dependency(
    unit_id: str,
    position: int,
    barrier_unit_id: str | None,
) -> HistoryUnitDependency:
    """Return a trivial immovable record for one out-of-scope pinned unit."""
    return HistoryUnitDependency(
        unit_id=unit_id,
        original_position=position,
        earliest_position=position,
        barrier_unit_id=barrier_unit_id,
        barrier="UNKNOWN" if position > 0 else None,
        detail="outside-movable-scope" if position > 0 else None,
    )


def analyze_history_dependencies(
    snapshot: HistorySnapshot,
) -> tuple[HistoryUnitDependency, ...]:
    """Return compact first-barrier evidence for every exact source unit.

    Backward-commutation evidence is computed only for units in the movable
    scope ``movable_base..tip``. Units in the pinned prefix
    ``base..movable_base`` receive trivial immovable records because the plan
    contract forbids splitting, reordering, or donating them.
    """
    movable_commit_start = snapshot.movable_commit_start
    movable_unit_start = sum(
        len(commit.units) for commit in snapshot.commits[:movable_commit_start]
    )
    boundary_tree = (
        snapshot.base_tree
        if movable_commit_start == 0
        else snapshot.commits[movable_commit_start - 1].tree
    )
    expected_unit_ids = tuple(
        unit.unit_id for commit in snapshot.commits for unit in commit.units
    )

    with (
        temporary_git_object_environment(disable_replace_objects=True) as quarantine,
        quarantine.pinned_environment() as env,
    ):
        with acquire_history_replay_units(
            snapshot, env=env, from_commit_index=movable_commit_start
        ) as units:
            replay_cache: dict[tuple[str, str], PatchApplicationResult] = {}
            dependencies: list[HistoryUnitDependency | None] = [None] * len(
                expected_unit_ids
            )
            for position in range(movable_unit_start):
                dependencies[position] = _pinned_dependency(
                    expected_unit_ids[position],
                    position,
                    expected_unit_ids[position - 1] if position > 0 else None,
                )

            segment_start = movable_unit_start
            segment_units: list[HistoryReplayUnit] = []
            segment_trees = [boundary_tree]
            segment_boundary_detail: str | None = (
                "outside-movable-scope" if movable_commit_start > 0 else None
            )
            unit_offset = movable_unit_start

            for commit in snapshot.commits[movable_commit_start:]:
                commit_start = unit_offset
                commit_end = commit_start + len(commit.units)
                local_start = commit_start - movable_unit_start
                local_end = commit_end - movable_unit_start
                trial_tree = segment_trees[-1]
                trial_trees: list[str] = []
                failure_detail: str | None = None
                for local_position in range(local_start, local_end):
                    result = apply_history_replay_unit(
                        trial_tree,
                        units[local_position],
                        env=env,
                        replay_cache=replay_cache,
                    )
                    if result.status != "APPLIED" or result.tree is None:
                        failure_detail = result.detail or result.status.lower()
                        break
                    trial_tree = result.tree
                    trial_trees.append(trial_tree)
                if failure_detail is None and trial_tree != commit.tree:
                    failure_detail = "source-units-do-not-reconstruct-commit-tree"

                if failure_detail is None:
                    segment_units.extend(units[local_start:local_end])
                    segment_trees.extend(trial_trees)
                else:
                    _record_replayable_segment(
                        dependencies,
                        expected_unit_ids,
                        segment_start,
                        tuple(segment_units),
                        tuple(segment_trees),
                        segment_boundary_detail,
                        env=env,
                        replay_cache=replay_cache,
                    )
                    for position in range(commit_start, commit_end):
                        dependencies[position] = _unknown_dependency(
                            expected_unit_ids[position],
                            position,
                            expected_unit_ids[position - 1] if position > 0 else None,
                            failure_detail,
                        )
                    segment_start = commit_end
                    segment_units = []
                    segment_trees = [commit.tree]
                    segment_boundary_detail = failure_detail
                unit_offset = commit_end

            _record_replayable_segment(
                dependencies,
                expected_unit_ids,
                segment_start,
                tuple(segment_units),
                tuple(segment_trees),
                segment_boundary_detail,
                env=env,
                replay_cache=replay_cache,
            )
            if any(dependency is None for dependency in dependencies):
                raise RuntimeError("history dependency analysis left a unit unrecorded")
            return tuple(
                dependency for dependency in dependencies if dependency is not None
            )
