"""Mechanical tree replay for validated rewrite-plan plans."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass

from ..exceptions import CommandError
from ..fixup.commutation import apply_patch_to_tree, load_tree_diff_as_buffer
from ..i18n import _
from ..utils.git_object_io import temporary_git_object_environment
from .models import (
    HistoryCommitSnapshot,
    HistoryPlanDocument,
    HistoryPlannedCommit,
)
from .unit_replay import (
    HistoryReplayUnit,
    acquire_history_replay_units,
    apply_history_replay_unit,
)


@dataclass(frozen=True, slots=True)
class HistoryReplayResult:
    """Exact output trees produced by one complete plan replay."""

    output_trees: tuple[str, ...]
    final_tree: str


def _requires_unit_replay(
    output: HistoryPlannedCommit,
    sources: dict[str, HistoryCommitSnapshot],
) -> bool:
    if output.operation in {"SPLIT", "REORDER"}:
        return True
    expected_units = tuple(
        unit.unit_id
        for source_commit in output.source_commits
        for unit in sources[source_commit].units
    )
    return output.unit_ids != expected_units


def _apply_whole_source_output(
    output: HistoryPlannedCommit,
    sources: dict[str, HistoryCommitSnapshot],
    current_tree: str,
    output_index: int,
    *,
    env: dict[str, str] | None = None,
) -> str:
    parent_tree = current_tree
    source_had_effect = False
    for source_commit in output.source_commits:
        source = sources[source_commit]
        if source.parent_tree == source.tree:
            continue
        source_had_effect = True
        with load_tree_diff_as_buffer(
            source.parent_tree,
            source.tree,
            env=env,
        ) as patch:
            replayed_tree = apply_patch_to_tree(
                current_tree,
                patch.byte_chunks(),
                three_way=True,
                env=env,
            )
        if replayed_tree is None:
            raise CommandError(
                _(
                    "Rewrite output {output} cannot replay source commit "
                    "{commit} at its requested position."
                ).format(output=output_index + 1, commit=source_commit)
            )
        current_tree = replayed_tree

    if (
        output.operation == "INTEGRATE"
        and source_had_effect
        and current_tree == parent_tree
    ):
        raise CommandError(
            _(
                "Rewrite output {output} integrates non-empty sources into "
                "an empty commit."
            ).format(output=output_index + 1)
        )
    return current_tree


def _apply_unit_output(
    output: HistoryPlannedCommit,
    units: dict[str, HistoryReplayUnit],
    current_tree: str,
    output_index: int,
    *,
    env: dict[str, str] | None,
) -> str:
    parent_tree = current_tree
    for unit_id in output.unit_ids:
        result = apply_history_replay_unit(
            current_tree,
            units[unit_id],
            env=env,
        )
        if result.status != "APPLIED" or result.tree is None:
            raise CommandError(
                _(
                    "Rewrite output {output} cannot replay unit {unit}: "
                    "{status} ({detail})."
                ).format(
                    output=output_index + 1,
                    unit=unit_id,
                    status=result.status,
                    detail=result.detail or "no detail",
                )
            )
        current_tree = result.tree
    if output.unit_ids and current_tree == parent_tree:
        raise CommandError(
            _(
                "Rewrite output {output} consumes non-empty units into "
                "an empty commit."
            ).format(output=output_index + 1)
        )
    return current_tree



def materialize_history_output_trees(
    document: HistoryPlanDocument,
    *,
    env: dict[str, str] | None = None,
) -> HistoryReplayResult:
    """Replay every consumed source patch once and require the frozen final tree."""
    sources = {commit.commit_id: commit for commit in document.snapshot.commits}
    unit_output_indexes = {
        index
        for index, output in enumerate(document.plan.outputs)
        if _requires_unit_replay(output, sources)
    }
    current_tree = document.snapshot.base_tree
    output_trees: list[str] = []
    with ExitStack() as stack:
        units: dict[str, HistoryReplayUnit] = {}
        if unit_output_indexes:
            acquired_units = stack.enter_context(
                acquire_history_replay_units(document.snapshot)
            )
            units = {unit.snapshot.unit_id: unit for unit in acquired_units}
        for output_index, output in enumerate(document.plan.outputs):
            if output_index in unit_output_indexes:
                current_tree = _apply_unit_output(
                    output,
                    units,
                    current_tree,
                    output_index,
                    env=env,
                )
            else:
                current_tree = _apply_whole_source_output(
                    output,
                    sources,
                    current_tree,
                    output_index,
                    env=env,
                )
            output_trees.append(current_tree)

    if current_tree != document.snapshot.final_tree:
        raise CommandError(
            _(
                "The planned history replay produces tree {actual}, not the "
                "frozen final tree {expected}."
            ).format(
                actual=current_tree,
                expected=document.snapshot.final_tree,
            )
        )
    return HistoryReplayResult(
        output_trees=tuple(output_trees),
        final_tree=current_tree,
    )


def validate_history_plan_materialization(
    document: HistoryPlanDocument,
) -> HistoryReplayResult:
    """Prove a plan in a temporary object quarantine."""
    with temporary_git_object_environment() as env:
        return materialize_history_output_trees(document, env=env)
