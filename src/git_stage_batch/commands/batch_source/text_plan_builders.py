"""Text action plan builders for batch-source commands."""

from __future__ import annotations

from dataclasses import dataclass
import os

from . import action_plans as _action_plans
from ...batch.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...core.buffer import LineBuffer
from ...core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_target_change_type,
)
from ...utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ...utils.git import get_git_repository_root_path


@dataclass(frozen=True)
class ApplyTextPlanBuildResult:
    """Result of building one apply-from text action plan."""

    plan: _action_plans.ApplyTextFileActionPlan | None = None
    missing_source: bool = False


def build_apply_text_file_action_plan(
    *,
    file_path: str,
    file_meta: dict,
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
) -> ApplyTextPlanBuildResult:
    """Build one deferred apply-from text action plan."""
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

    repo_root = get_git_repository_root_path()
    working_exists = os.path.lexists(repo_root / file_path)

    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selected_ids,
        destination_exists=working_exists,
    )
    if selected_ids is None and text_change_type == TextFileChangeType.DELETED:
        return ApplyTextPlanBuildResult(
            plan=_action_plans.ApplyTextFileActionPlan(
                file_path,
                None,
                file_mode,
                text_change_type,
            )
        )

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        return ApplyTextPlanBuildResult(missing_source=True)

    with (
        batch_source_buffer as batch_source_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            if ownership.is_empty():
                if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                    merged_buffer = LineBuffer.from_bytes(b"")
                else:
                    return ApplyTextPlanBuildResult()
            else:
                merged_buffer = merge_batch_from_line_sequences_as_buffer(
                    batch_source_lines,
                    ownership,
                    working_lines,
                )

    effective_change_type = selected_text_target_change_type(
        text_change_type,
        selected_ids,
        merged_buffer,
    )
    return ApplyTextPlanBuildResult(
        plan=_action_plans.ApplyTextFileActionPlan(
            file_path,
            merged_buffer,
            file_mode,
            effective_change_type,
        )
    )
