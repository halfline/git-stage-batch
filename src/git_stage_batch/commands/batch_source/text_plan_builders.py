"""Text action plan builders for batch-source commands."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path

from . import action_plans as _action_plans
from ...batch.discard import discard_batch_from_line_sequences_as_buffer
from ...batch.merge.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...core.buffer import LineBuffer
from ...core.replacement import ReplacementPayload
from ...core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_discard_change_type,
    selected_text_target_change_type,
)
from ...data.file_target_identity import IndexIdentity
from ...data.file_modes import detect_file_mode_in_commit
from ...utils.repository_buffers import (
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...utils.git_repository import get_git_repository_root_path


@dataclass(frozen=True)
class ApplyTextPlanBuildResult:
    """Result of building one apply-from text action plan."""

    plan: _action_plans.ApplyTextFileActionPlan | None = None
    missing_source: bool = False


@dataclass(frozen=True)
class IncludeTextPlanBuildResult:
    """Result of building one include-from text action plan."""

    plan: _action_plans.IncludeTextFileActionPlan | None = None
    missing_source: bool = False


@dataclass(frozen=True)
class DiscardTextPlanBuildResult:
    """Result of building one discard-from text action plan."""

    plan: _action_plans.DiscardTextFileActionPlan | None = None
    missing_source: bool = False


def _close_include_merge_buffers(
    index_buffer: LineBuffer | None,
    working_buffer: LineBuffer | None,
) -> None:
    if index_buffer is not None:
        index_buffer.close()
    if working_buffer is not None and working_buffer is not index_buffer:
        working_buffer.close()


def apply_text_plan_requires_source(
    file_meta: dict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one apply text plan needs batch source content."""
    return not (
        selected_ids is None
        and normalized_text_change_type(file_meta.get("change_type"))
        == TextFileChangeType.DELETED
    )


def include_text_plan_requires_source(
    file_meta: dict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one include text plan needs batch source content."""
    return apply_text_plan_requires_source(file_meta, selected_ids)


def build_apply_text_file_action_plan(
    *,
    file_path: str,
    file_meta: dict,
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
    batch_source_object_id: str | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    spool_dir: str | Path | None = None,
) -> ApplyTextPlanBuildResult:
    """Build one deferred apply-from text action plan."""
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

    if captured_working_tree_exists is None:
        repo_root = get_git_repository_root_path()
        working_exists = os.path.lexists(repo_root / file_path)
    else:
        working_exists = captured_working_tree_exists

    file_mode = mode_for_text_materialization(
        str(file_meta.get("mode", "100644")),
        selected_ids,
        destination_exists=working_exists,
    )
    if not apply_text_plan_requires_source(file_meta, selected_ids):
        return ApplyTextPlanBuildResult(
            plan=_action_plans.ApplyTextFileActionPlan(
                file_path,
                None,
                file_mode,
                text_change_type,
            )
        )

    batch_source_commit = file_meta["batch_source_commit"]
    if batch_source_object_id is None:
        batch_source_spec = f"{batch_source_commit}:{file_path}"
        batch_source_buffer = (
            read_git_object_buffer_or_none(batch_source_spec)
            if spool_dir is None
            else read_git_object_buffer_or_none(
                batch_source_spec,
                spool_dir=spool_dir,
            )
        )
    else:
        batch_source_buffer = load_git_blob_as_buffer(
            batch_source_object_id,
            spool_dir=spool_dir,
        )
    if batch_source_buffer is None:
        return ApplyTextPlanBuildResult(missing_source=True)

    with ExitStack() as stack:
        batch_source_lines = stack.enter_context(batch_source_buffer)
        if working_tree_artifact_path is None:
            working_tree_buffer = (
                load_working_tree_file_as_buffer(file_path)
                if spool_dir is None
                else load_working_tree_file_as_buffer(
                    file_path,
                    spool_dir=spool_dir,
                )
            )
        else:
            working_tree_buffer = LineBuffer.from_path(
                working_tree_artifact_path,
                spool_dir=spool_dir,
            )
        working_lines = stack.enter_context(working_tree_buffer)
        ownership_arguments = {}
        if spool_dir is not None:
            ownership_arguments["spool_dir"] = spool_dir
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
            **ownership_arguments,
        ) as ownership:
            if ownership.is_empty():
                if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                    merged_buffer = LineBuffer.from_bytes(
                        b"",
                        spool_dir=spool_dir,
                    )
                else:
                    return ApplyTextPlanBuildResult()
            else:
                merge_arguments = {}
                if spool_dir is not None:
                    merge_arguments["spool_dir"] = spool_dir
                merged_buffer = merge_batch_from_line_sequences_as_buffer(
                    batch_source_lines,
                    ownership,
                    working_lines,
                    **merge_arguments,
                )

    try:
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
    except BaseException:
        merged_buffer.close()
        raise


def build_include_text_file_action_plan(
    *,
    file_path: str,
    file_meta: dict,
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
    batch_source_object_id: str | None = None,
    captured_index_identity: IndexIdentity | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    spool_dir: str | Path | None = None,
) -> IncludeTextPlanBuildResult:
    """Build one deferred include-from text action plan."""
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))

    if captured_index_identity is None:
        index_buffer = read_git_object_buffer_or_none(f":{file_path}")
        index_exists = index_buffer is not None
    else:
        index_exists = captured_index_identity.exists
        index_buffer = None

    if captured_working_tree_exists is None:
        repo_root = get_git_repository_root_path()
        working_exists = os.path.lexists(repo_root / file_path)
    else:
        working_exists = captured_working_tree_exists

    batch_file_mode = str(file_meta.get("mode", "100644"))
    index_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selected_ids,
        destination_exists=index_exists,
    )
    working_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selected_ids,
        destination_exists=working_exists,
    )
    if not include_text_plan_requires_source(file_meta, selected_ids):
        if index_buffer is not None:
            index_buffer.close()
        return IncludeTextPlanBuildResult(
            plan=_action_plans.IncludeTextFileActionPlan(
                file_path,
                None,
                None,
                index_file_mode,
                working_file_mode,
                text_change_type,
                text_change_type,
            )
        )

    if (
        index_buffer is None
        and captured_index_identity is not None
        and captured_index_identity.content_object_id is not None
    ):
        index_buffer = load_git_blob_as_buffer(
            captured_index_identity.content_object_id,
            spool_dir=spool_dir,
        )
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"", spool_dir=spool_dir)

    batch_source_commit = file_meta["batch_source_commit"]
    if batch_source_object_id is None:
        batch_source_buffer = read_git_object_buffer_or_none(
            f"{batch_source_commit}:{file_path}"
        )
    else:
        batch_source_buffer = load_git_blob_as_buffer(
            batch_source_object_id,
            spool_dir=spool_dir,
        )
    if batch_source_buffer is None:
        index_buffer.close()
        return IncludeTextPlanBuildResult(missing_source=True)

    merged_index_buffer = None
    merged_working_buffer = None
    try:
        with ExitStack() as resources:
            batch_source_lines = resources.enter_context(batch_source_buffer)
            index_lines = resources.enter_context(index_buffer)
            if working_tree_artifact_path is None:
                working_buffer = (
                    load_working_tree_file_as_buffer(file_path)
                    if spool_dir is None
                    else load_working_tree_file_as_buffer(
                        file_path,
                        spool_dir=spool_dir,
                    )
                )
            else:
                working_buffer = LineBuffer.from_path(
                    working_tree_artifact_path,
                    spool_dir=spool_dir,
                )
            working_lines = resources.enter_context(working_buffer)
            ownership_arguments = {}
            if spool_dir is not None:
                ownership_arguments["spool_dir"] = spool_dir
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_include,
                **ownership_arguments,
            ) as ownership:
                if ownership.is_empty():
                    if (
                        selected_ids is None
                        and text_change_type == TextFileChangeType.ADDED
                    ):
                        merged_index_buffer = LineBuffer.from_bytes(
                            b"",
                            spool_dir=spool_dir,
                        )
                        merged_working_buffer = LineBuffer.from_bytes(
                            b"",
                            spool_dir=spool_dir,
                        )
                    else:
                        return IncludeTextPlanBuildResult()
                else:
                    with ExitStack() as stack:
                        source_lines = batch_source_lines
                        merge_ownership = ownership
                        if replacement_payload is not None:
                            replacement_arguments = {}
                            if spool_dir is not None:
                                replacement_arguments["spool_dir"] = spool_dir
                            replacement_view = stack.enter_context(
                                build_replacement_batch_view_from_lines(
                                    batch_source_lines,
                                    ownership,
                                    replacement_payload,
                                    **replacement_arguments,
                                )
                            )
                            source_lines = replacement_view.source_buffer
                            merge_ownership = replacement_view.ownership
                        merge_arguments = {}
                        if spool_dir is not None:
                            merge_arguments["spool_dir"] = spool_dir
                        merged_index_buffer = merge_batch_from_line_sequences_as_buffer(
                            source_lines,
                            merge_ownership,
                            index_lines,
                            **merge_arguments,
                        )
                        merged_working_buffer = (
                            merge_batch_from_line_sequences_as_buffer(
                                source_lines,
                                merge_ownership,
                                working_lines,
                                **merge_arguments,
                            )
                        )

        index_change_type = selected_text_target_change_type(
            text_change_type,
            selected_ids,
            merged_index_buffer,
        )
        working_change_type = selected_text_target_change_type(
            text_change_type,
            selected_ids,
            merged_working_buffer,
        )
        plan = _action_plans.IncludeTextFileActionPlan(
            file_path,
            merged_index_buffer,
            merged_working_buffer,
            index_file_mode,
            working_file_mode,
            index_change_type,
            working_change_type,
        )
        merged_index_buffer = None
        merged_working_buffer = None
        return IncludeTextPlanBuildResult(plan=plan)
    except BaseException:
        _close_include_merge_buffers(merged_index_buffer, merged_working_buffer)
        raise


def build_discard_text_file_action_plan(
    *,
    file_path: str,
    file_meta: dict,
    baseline_commit: str,
    selected_ids: set[int] | None,
    selection_ids_to_discard: set[int] | None,
) -> DiscardTextPlanBuildResult:
    """Build one deferred discard-from text action plan."""
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    if selected_ids is None and text_change_type in {
        TextFileChangeType.ADDED,
        TextFileChangeType.DELETED,
    }:
        return DiscardTextPlanBuildResult(
            plan=_build_baseline_restore_text_plan(
                file_path=file_path,
                baseline_commit=baseline_commit,
            )
        )

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = read_git_object_buffer_or_none(
        f"{batch_source_commit}:{file_path}"
    )
    if batch_source_buffer is None:
        return DiscardTextPlanBuildResult(missing_source=True)

    baseline_buffer = read_git_object_buffer_or_none(f"{baseline_commit}:{file_path}")
    baseline_exists = baseline_buffer is not None
    if baseline_buffer is None:
        baseline_buffer = LineBuffer.from_bytes(b"")

    repo_root = get_git_repository_root_path()
    working_exists = (repo_root / file_path).exists()
    baseline_mode = detect_file_mode_in_commit(baseline_commit, file_path)
    restore_mode = mode_for_text_materialization(
        baseline_mode,
        selected_ids,
        destination_exists=working_exists,
    )

    discarded_buffer = None
    try:
        with (
            batch_source_buffer as batch_source_lines,
            baseline_buffer as baseline_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_discard,
            ) as ownership:
                if ownership.is_empty():
                    return DiscardTextPlanBuildResult()

                discarded_buffer = discard_batch_from_line_sequences_as_buffer(
                    batch_source_lines,
                    ownership,
                    working_lines,
                    baseline_lines,
                )

        effective_change_type = selected_text_discard_change_type(
            text_change_type,
            selected_ids,
            discarded_buffer,
            baseline_exists=baseline_exists,
        )
        if effective_change_type == TextFileChangeType.DELETED:
            discarded_buffer.close()
            discarded_buffer = None
        plan = _action_plans.DiscardTextFileActionPlan(
            file_path,
            discarded_buffer,
            restore_mode,
            effective_change_type,
        )
        discarded_buffer = None
        return DiscardTextPlanBuildResult(plan=plan)
    except Exception:
        if discarded_buffer is not None:
            discarded_buffer.close()
        raise


def _build_baseline_restore_text_plan(
    *,
    file_path: str,
    baseline_commit: str,
) -> _action_plans.DiscardTextFileActionPlan:
    baseline_buffer = read_git_object_buffer_or_none(f"{baseline_commit}:{file_path}")
    if baseline_buffer is None:
        return _action_plans.DiscardTextFileActionPlan(
            file_path,
            None,
            None,
            TextFileChangeType.DELETED,
        )
    return _action_plans.DiscardTextFileActionPlan(
        file_path,
        baseline_buffer,
        detect_file_mode_in_commit(baseline_commit, file_path),
        TextFileChangeType.MODIFIED,
    )
