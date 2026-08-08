"""Mechanical tree replay for validated rewrite-plan plans."""

from __future__ import annotations

from dataclasses import dataclass

from ..exceptions import CommandError
from ..fixup.commutation import apply_patch_to_tree, load_tree_diff_as_buffer
from ..i18n import _
from ..utils.git_object_io import temporary_git_object_environment
from .models import HistoryPlanDocument


@dataclass(frozen=True, slots=True)
class HistoryReplayResult:
    """Exact output trees produced by one complete plan replay."""

    output_trees: tuple[str, ...]
    final_tree: str


def materialize_history_output_trees(
    document: HistoryPlanDocument,
    *,
    env: dict[str, str] | None = None,
) -> HistoryReplayResult:
    """Replay every consumed source patch once and require the frozen final tree."""
    sources = {commit.commit_id: commit for commit in document.snapshot.commits}
    current_tree = document.snapshot.base_tree
    output_trees: list[str] = []
    for output_index, output in enumerate(document.plan.outputs):
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
