"""Mechanical tree replay for validated rewrite-plan plans."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Protocol

from ..exceptions import CommandError
from ..fixup.commutation import (
    PatchApplicationResult,
    apply_patch_to_tree,
    load_tree_diff_as_buffer,
)
from ..i18n import _
from ..utils.git_object_io import (
    get_git_object_type,
    temporary_git_object_environment,
)
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


class HistoryResolvedOutputMaterializer(Protocol):
    """Build one explicit output tree from its actual replay parent."""

    def __call__(
        self,
        document: HistoryPlanDocument,
        output_index: int,
        output: HistoryPlannedCommit,
        parent_tree: str,
        *,
        env: dict[str, str] | None,
    ) -> str: ...


def _require_resolved_tree(
    document: HistoryPlanDocument,
    output_index: int,
    candidate: object,
    *,
    env: dict[str, str] | None,
) -> str:
    oid_length = 40 if document.snapshot.object_format == "sha1" else 64
    if (
        not isinstance(candidate, str)
        or len(candidate) != oid_length
        or any(character not in "0123456789abcdef" for character in candidate)
    ):
        raise CommandError(
            _(
                "Rewrite output {output} resolution did not produce a full "
                "tree object ID."
            ).format(output=output_index + 1)
        )
    if get_git_object_type(candidate, env=env) != "tree":
        raise CommandError(
            _(
                "Rewrite output {output} resolution did not produce an "
                "accessible tree object."
            ).format(output=output_index + 1)
        )
    return candidate


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
    return output.source_unit_ids != expected_units


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
    replay_cache: dict[tuple[str, str], PatchApplicationResult] | None = None,
) -> str:
    parent_tree = current_tree
    for unit_id in output.source_unit_ids:
        result = apply_history_replay_unit(
            current_tree,
            units[unit_id],
            env=env,
            replay_cache=replay_cache,
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
    if output.source_unit_ids and current_tree == parent_tree:
        raise CommandError(
            _(
                "Rewrite output {output} consumes non-empty units into an empty commit."
            ).format(output=output_index + 1)
        )
    return current_tree


def materialize_history_output_trees(
    document: HistoryPlanDocument,
    *,
    env: dict[str, str] | None = None,
    resolved_output_materializer: HistoryResolvedOutputMaterializer | None = None,
) -> HistoryReplayResult:
    """Replay every consumed source patch once and require the frozen final tree."""
    resolved_output = next(
        (
            index
            for index, output in enumerate(document.plan.outputs)
            if output.materialization == "RESOLVED"
        ),
        None,
    )
    if resolved_output is not None and resolved_output_materializer is None:
        raise CommandError(
            _(
                "Rewrite output {output} requires an explicit resolution workspace."
            ).format(output=resolved_output + 1)
        )
    sources = {commit.commit_id: commit for commit in document.snapshot.commits}
    unit_output_indexes = {
        index
        for index, output in enumerate(document.plan.outputs)
        if output.materialization == "EXACT" and _requires_unit_replay(output, sources)
    }
    current_tree = document.snapshot.base_tree
    output_trees: list[str] = []
    replay_cache: dict[tuple[str, str], PatchApplicationResult] = {}
    with ExitStack() as stack:
        units: dict[str, HistoryReplayUnit] = {}
        if unit_output_indexes:
            acquired_units = stack.enter_context(
                acquire_history_replay_units(document.snapshot, env=env)
            )
            units = {unit.snapshot.unit_id: unit for unit in acquired_units}
        for output_index, output in enumerate(document.plan.outputs):
            parent_tree = current_tree
            if output.materialization == "RESOLVED":
                if resolved_output_materializer is None:
                    raise AssertionError("resolved materializer was checked above")
                current_tree = _require_resolved_tree(
                    document,
                    output_index,
                    resolved_output_materializer(
                        document,
                        output_index,
                        output,
                        parent_tree,
                        env=env,
                    ),
                    env=env,
                )
                if current_tree == parent_tree:
                    raise CommandError(
                        _(
                            "Rewrite output {output} resolves non-empty units "
                            "into an empty commit."
                        ).format(output=output_index + 1)
                    )
            elif output_index in unit_output_indexes:
                current_tree = _apply_unit_output(
                    output,
                    units,
                    current_tree,
                    output_index,
                    env=env,
                    replay_cache=replay_cache,
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
    with temporary_git_object_environment(disable_replace_objects=True) as quarantine:
        with quarantine.pinned_environment() as environment:
            return materialize_history_output_trees(
                document,
                env=environment,
            )
