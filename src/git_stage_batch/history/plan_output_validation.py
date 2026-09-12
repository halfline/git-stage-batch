"""Enforced source selection and target constraints for one output."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from .models import (
    HistoryCommitSnapshot,
    HistoryPlannedCommit,
)
from .plan_source_index import PlanSourceIndex


def validate_plan_output(
    index: int,
    output: HistoryPlannedCommit,
    source_index: PlanSourceIndex,
    pinned_commit_ids: set[str],
    unit_occurrences: dict[str, list[int]],
    _invalid: Callable[[str], NoReturn],
) -> tuple[HistoryCommitSnapshot, ...]:
    """Validate an output, recording each selected unit exactly once."""
    source_by_id = source_index.source_by_id
    source_positions = source_index.source_positions
    unit_by_id = source_index.unit_by_id
    unit_positions_by_source = source_index.unit_positions
    location = f"plan.outputs[{index}]"
    if not output.source_commits:
        _invalid(f"{location}.source_commits must not be empty")
    if any(commit not in source_by_id for commit in output.source_commits):
        _invalid(f"{location}.source_commits contains an unknown commit")
    if len(set(output.source_commits)) != len(output.source_commits):
        _invalid(f"{location}.source_commits must not contain duplicates")
    positions = tuple(source_positions[commit] for commit in output.source_commits)
    if positions != tuple(sorted(positions)):
        _invalid(f"{location}.source_commits must retain source order")
    if (
        output.operation in {"KEEP", "REWORD", "SPLIT", "REORDER"}
        and len(positions) != 1
    ):
        _invalid(f"{location}.{output.operation} must consume one source commit")
    if output.operation == "INTEGRATE" and len(positions) < 2:
        _invalid(f"{location}.INTEGRATE must consume at least two commits")
    if output.materialization == "RESOLVED" and not output.source_unit_ids:
        _invalid(f"{location}.RESOLVED must declare at least one source unit")

    sources = tuple(source_by_id[commit] for commit in output.source_commits)
    unknown_units = [
        unit_id for unit_id in output.source_unit_ids if unit_id not in unit_by_id
    ]
    if unknown_units:
        _invalid(f"{location}.source_unit_ids contains an unknown unit")
    if len(set(output.source_unit_ids)) != len(output.source_unit_ids):
        _invalid(f"{location}.source_unit_ids must not contain duplicates")
    selected_keys: list[tuple[int, int]] = []
    selected_by_source: dict[str, list[str]] = {
        source.commit_id: [] for source in sources
    }
    source_order = {
        source.commit_id: source_index for source_index, source in enumerate(sources)
    }
    for unit_id in output.source_unit_ids:
        unit = unit_by_id[unit_id]
        if unit.source_commit not in source_order:
            _invalid(
                f"{location}.source_unit_ids contains a unit from an unlisted source"
            )
        selected_by_source[unit.source_commit].append(unit_id)
        unit_occurrences[unit_id].append(index)
        selected_keys.append(
            (
                source_order[unit.source_commit],
                unit_positions_by_source[unit.source_commit][unit_id],
            )
        )
    if selected_keys != sorted(selected_keys):
        _invalid(f"{location}.source_unit_ids must retain source and unit order")
    for source in sources:
        if source.units and not selected_by_source[source.commit_id]:
            _invalid(
                f"{location} lists source {source.commit_id} without any of its units"
            )
    _validate_output_target(
        index,
        output,
        sources,
        selected_by_source,
        pinned_commit_ids,
        _invalid,
    )
    return sources


def _validate_output_target(
    index: int,
    output: HistoryPlannedCommit,
    sources: tuple[HistoryCommitSnapshot, ...],
    selected_by_source: dict[str, list[str]],
    pinned_commit_ids: set[str],
    _invalid: Callable[[str], NoReturn],
) -> None:
    """Require pinned-source, author, unit-shape, and message invariants."""
    location = f"plan.outputs[{index}]"
    target_source = sources[0]
    if target_source.commit_id in pinned_commit_ids and output.operation in {
        "SPLIT",
        "REORDER",
    }:
        _invalid(
            f"{location}.{output.operation} may not restructure pinned "
            f"source commit {target_source.commit_id} outside the movable "
            "scope"
        )
    pinned_secondary = next(
        (source for source in sources[1:] if source.commit_id in pinned_commit_ids),
        None,
    )
    if pinned_secondary is not None:
        _invalid(
            f"{location} may not consume pinned source commit "
            f"{pinned_secondary.commit_id} as a donor; pinned commits "
            "outside the movable scope may only receive units through "
            "INTEGRATE"
        )
    unsupported_source = next(
        (source for source in sources if source.unsupported_headers),
        None,
    )
    if unsupported_source is not None:
        _invalid(
            f"source commit {unsupported_source.commit_id} has unsupported "
            "header(s): "
            f"{', '.join(unsupported_source.unsupported_headers)}"
        )
    if output.author != target_source.author:
        _invalid(f"{location}.author must preserve the target author")
    target_units = tuple(unit.unit_id for unit in target_source.units)
    selected_target_units = tuple(selected_by_source[target_source.commit_id])
    if output.operation in {"KEEP", "REWORD", "REORDER"}:
        if output.source_unit_ids != target_units:
            _invalid(
                f"{location}.{output.operation} must consume every target unit in order"
            )
    elif output.operation == "SPLIT":
        if not output.source_unit_ids:
            _invalid(f"{location}.SPLIT must contain at least one unit")
    elif selected_target_units != target_units:
        _invalid(f"{location}.INTEGRATE must consume every target unit in order")

    if output.operation in {"KEEP", "REORDER"}:
        if output.message != target_source.message:
            _invalid(
                f"{location}.message changed without a REWORD, SPLIT, or "
                "INTEGRATE operation"
            )
        if output.encoding != target_source.encoding:
            _invalid(
                f"{location}.encoding changed without a REWORD, SPLIT, or "
                "INTEGRATE operation"
            )
