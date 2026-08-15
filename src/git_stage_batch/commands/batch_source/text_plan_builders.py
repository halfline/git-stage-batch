"""Text action plan builders for batch-source commands."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
from types import TracebackType
from typing import TypedDict, cast

from . import action_plans as _action_plans
from ...batch.discard import discard_batch_from_line_sequences_as_buffer
from ...batch.merge.merge import merge_batch_from_line_sequences_as_buffer
from ...batch.merge.legacy_intent import (
    reject_ambiguous_legacy_presence_replay,
)
from ...batch.merge.baseline_replacement_edits import (
    trusted_target_replacement_source_ranges,
)
from ...batch.line_matching.match import match_lines
from ...batch.replacement import build_replacement_batch_view_from_lines
from ...batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from ...batch.state.metadata_types import BatchFileMetadataDict
from ...batch.ownership.metadata_types import BatchOwnershipMetadata
from ...batch.ownership.attribution_metadata import (
    compact_ownership_metadata_for_attribution,
)
from ...core.buffer import LineBuffer
from ...core.line_selection import LineRanges
from ...core.replacement import ReplacementPayload
from ...core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_discard_change_type,
    selected_text_target_change_type,
)
from ...core.text_lines import normalize_line_sequence_endings
from ...data.file_target_identity import IndexIdentity
from ...data.applied_batch_overlays import (
    selected_presence_was_introduced,
)
from ...data.file_modes import detect_file_mode_in_commit
from ...utils.repository_buffers import (
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
    load_working_tree_file_as_buffer,
)
from ...utils.git_repository import get_git_repository_root_path


class _SpoolDirOptions(TypedDict, total=False):
    """Typed optional arguments for spool-aware helpers."""

    spool_dir: str | Path


def _spool_dir_options(spool_dir: str | Path | None) -> _SpoolDirOptions:
    if spool_dir is None:
        return {}
    return {"spool_dir": spool_dir}


@dataclass(frozen=True)
class ApplyTextPlanBuildResult:
    """Result of building one apply-from text action plan."""

    plan: _action_plans.ApplyTextFileActionPlan | None = None
    missing_source: bool = False
    selected_ownership_metadata: BatchOwnershipMetadata | None = None
    introduced_selected_presence: bool = False
    index_preimage_source_ranges: tuple[tuple[int, int], ...] = ()


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


def apply_text_plan_requires_source(
    file_meta: BatchFileMetadataDict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one apply text plan needs batch source content."""
    return not (
        selected_ids is None
        and normalized_text_change_type(file_meta.get("change_type"))
        == TextFileChangeType.DELETED
    )


def include_text_plan_requires_source(
    file_meta: BatchFileMetadataDict,
    selected_ids: set[int] | None,
) -> bool:
    """Return whether one include text plan needs batch source content."""
    return apply_text_plan_requires_source(file_meta, selected_ids)


def build_apply_text_file_action_plan(
    *,
    file_path: str,
    file_meta: BatchFileMetadataDict,
    selected_ids: set[int] | None,
    selection_ids_to_apply: set[int] | None,
    batch_source_object_id: str | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    captured_index_identity: IndexIdentity | None = None,
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
        selected_ownership = compact_ownership_metadata_for_attribution(
            cast(BatchOwnershipMetadata, file_meta),
        )
        return ApplyTextPlanBuildResult(
            plan=_action_plans.ApplyTextFileActionPlan(
                file_path,
                None,
                file_mode,
                text_change_type,
            ),
            selected_ownership_metadata=selected_ownership,
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

    merged_buffer: LineBuffer | None = None
    result: ApplyTextPlanBuildResult | None = None

    def close_merged_buffer_on_context_error(
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        nonlocal merged_buffer
        if exception_type is not None and merged_buffer is not None:
            buffer = merged_buffer
            merged_buffer = None
            buffer.close()
        return False

    with ExitStack() as stack:
        # Register first so this runs after ownership and every source/mapping
        # resource has exited, with any exit failure still visible.
        stack.push(close_merged_buffer_on_context_error)
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
        spool_options = _spool_dir_options(spool_dir)
        introduced_selected_presence = False
        index_preimage_source_ranges: tuple[tuple[int, int], ...] = ()
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
            **spool_options,
        ) as ownership:
            reject_ambiguous_legacy_presence_replay(
                file_path,
                batch_source_lines,
                ownership,
                working_lines,
                legacy_unmarked_source_alternatives=(
                    file_meta.get("legacy_unmarked_source_alternatives") is True
                    and selection_ids_to_apply is None
                ),
                spool_dir=spool_dir,
            )
            selected_ownership_metadata = ownership.to_attribution_metadata_dict()
            try:
                if ownership.is_empty():
                    if (
                        selected_ids is None
                        and text_change_type == TextFileChangeType.ADDED
                    ):
                        merged_buffer = LineBuffer.from_bytes(
                            b"",
                            spool_dir=spool_dir,
                        )
                    else:
                        return ApplyTextPlanBuildResult()
                else:
                    has_replacement_origin = any(
                        unit.origin is not None for unit in ownership.replacement_units
                    )
                    if not has_replacement_origin:
                        trusted_target_buffer = None
                    elif captured_index_identity is None:
                        trusted_target_buffer = read_git_object_buffer_or_none(
                            f":{file_path}",
                            **spool_options,
                        )
                    elif captured_index_identity.content_object_id is None:
                        trusted_target_buffer = None
                    else:
                        trusted_target_buffer = load_git_blob_as_buffer(
                            captured_index_identity.content_object_id,
                            spool_dir=spool_dir,
                        )
                    trusted_target_lines = (
                        None
                        if trusted_target_buffer is None
                        else stack.enter_context(trusted_target_buffer)
                    )
                    source_to_working_mapping = None
                    source_to_trusted_target_mapping = None
                    trusted_target_to_working_mapping = None
                    if trusted_target_lines is not None:
                        normalized_source_lines = normalize_line_sequence_endings(
                            batch_source_lines
                        )
                        normalized_working_lines = normalize_line_sequence_endings(
                            working_lines
                        )
                        normalized_trusted_target_lines = (
                            normalize_line_sequence_endings(trusted_target_lines)
                        )
                        source_to_working_mapping = stack.enter_context(
                            match_lines(
                                normalized_source_lines,
                                normalized_working_lines,
                                **spool_options,
                            )
                        )
                        source_to_trusted_target_mapping = stack.enter_context(
                            match_lines(
                                normalized_source_lines,
                                normalized_trusted_target_lines,
                                **spool_options,
                            )
                        )
                        trusted_target_to_working_mapping = stack.enter_context(
                            match_lines(
                                normalized_trusted_target_lines,
                                normalized_working_lines,
                                **spool_options,
                            )
                        )
                    merged_buffer = merge_batch_from_line_sequences_as_buffer(
                        batch_source_lines,
                        ownership,
                        working_lines,
                        trusted_target_lines=trusted_target_lines,
                        source_to_working_mapping=source_to_working_mapping,
                        source_to_trusted_target_mapping=(
                            source_to_trusted_target_mapping
                        ),
                        trusted_target_to_working_mapping=(
                            trusted_target_to_working_mapping
                        ),
                        **spool_options,
                    )
                    if (
                        trusted_target_lines is not None
                        and source_to_working_mapping is not None
                        and source_to_trusted_target_mapping is not None
                        and trusted_target_to_working_mapping is not None
                    ):
                        index_preimage_source_ranges = (
                            trusted_target_replacement_source_ranges(
                                normalized_source_lines,
                                ownership,
                                normalized_working_lines,
                                normalized_trusted_target_lines,
                                source_to_working_mapping,
                                source_to_trusted_target_mapping,
                                trusted_target_to_working_mapping,
                                spool_dir=spool_dir,
                            ).ranges()
                        )
                introduced_selected_presence = selected_presence_was_introduced(
                    cast(
                        BatchFileMetadataDict,
                        selected_ownership_metadata,
                    ),
                    batch_source_lines,
                    working_lines,
                    merged_buffer,
                )
                effective_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    merged_buffer,
                )
                result = ApplyTextPlanBuildResult(
                    plan=_action_plans.ApplyTextFileActionPlan(
                        file_path,
                        merged_buffer,
                        file_mode,
                        effective_change_type,
                        expected_index_identity=captured_index_identity,
                    ),
                    selected_ownership_metadata=selected_ownership_metadata,
                    introduced_selected_presence=introduced_selected_presence,
                    index_preimage_source_ranges=index_preimage_source_ranges,
                )
            except BaseException:
                if merged_buffer is not None:
                    buffer = merged_buffer
                    merged_buffer = None
                    buffer.close()
                raise

    assert result is not None
    merged_buffer = None
    return result


def build_include_text_file_action_plan(
    *,
    file_path: str,
    file_meta: BatchFileMetadataDict,
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

    try:
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
    except BaseException:
        if index_buffer is not None:
            index_buffer.close()
        raise

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
            spool_options = _spool_dir_options(spool_dir)
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_include,
                **spool_options,
            ) as ownership:
                legacy_unmarked_source_alternatives = (
                    file_meta.get("legacy_unmarked_source_alternatives") is True
                    and selection_ids_to_include is None
                )
                reject_ambiguous_legacy_presence_replay(
                    file_path,
                    batch_source_lines,
                    ownership,
                    index_lines,
                    legacy_unmarked_source_alternatives=(
                        legacy_unmarked_source_alternatives
                    ),
                    spool_dir=spool_dir,
                )
                reject_ambiguous_legacy_presence_replay(
                    file_path,
                    batch_source_lines,
                    ownership,
                    working_lines,
                    legacy_unmarked_source_alternatives=(
                        legacy_unmarked_source_alternatives
                    ),
                    spool_dir=spool_dir,
                )
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
                            replacement_view = stack.enter_context(
                                build_replacement_batch_view_from_lines(
                                    batch_source_lines,
                                    ownership,
                                    replacement_payload,
                                    **spool_options,
                                )
                            )
                            source_lines = replacement_view.source_buffer
                            merge_ownership = replacement_view.ownership
                        merged_index_buffer = merge_batch_from_line_sequences_as_buffer(
                            source_lines,
                            merge_ownership,
                            index_lines,
                            **spool_options,
                        )
                        merged_working_buffer = (
                            merge_batch_from_line_sequences_as_buffer(
                                source_lines,
                                merge_ownership,
                                working_lines,
                                **spool_options,
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
        merge_buffers = []
        if merged_index_buffer is not None:
            merge_buffers.append(merged_index_buffer)
        if (
            merged_working_buffer is not None
            and merged_working_buffer is not merged_index_buffer
        ):
            merge_buffers.append(merged_working_buffer)
        _action_plans.close_resources(merge_buffers)
        raise


def build_discard_text_file_action_plan(
    *,
    file_path: str,
    file_meta: BatchFileMetadataDict,
    baseline_commit: str,
    selected_ids: set[int] | None,
    selection_ids_to_discard: set[int] | None,
    trusted_presence_lines: LineRanges | None = None,
    applied_presence_lines: LineRanges | None = None,
    index_preimage_presence_lines: LineRanges | None = None,
    captured_index_identity: IndexIdentity | None = None,
    working_tree_artifact_path: str | Path | None = None,
    captured_working_tree_exists: bool | None = None,
    spool_dir: str | Path | None = None,
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
                spool_dir=spool_dir,
            )
        )

    discarded_buffer = None
    try:
        with ExitStack() as stack:
            batch_source_commit = file_meta["batch_source_commit"]
            batch_source_buffer = read_git_object_buffer_or_none(
                f"{batch_source_commit}:{file_path}",
                **_spool_dir_options(spool_dir),
            )
            if batch_source_buffer is None:
                return DiscardTextPlanBuildResult(missing_source=True)
            batch_source_lines = stack.enter_context(batch_source_buffer)

            baseline_buffer = read_git_object_buffer_or_none(
                f"{baseline_commit}:{file_path}",
                **_spool_dir_options(spool_dir),
            )
            baseline_exists = baseline_buffer is not None
            if baseline_buffer is None:
                baseline_buffer = LineBuffer.from_bytes(
                    b"",
                    spool_dir=spool_dir,
                )
            baseline_lines = stack.enter_context(baseline_buffer)

            if captured_working_tree_exists is None:
                repo_root = get_git_repository_root_path()
                working_exists = (repo_root / file_path).exists()
            else:
                working_exists = captured_working_tree_exists
            baseline_mode = detect_file_mode_in_commit(
                baseline_commit,
                file_path,
            )
            restore_mode = mode_for_text_materialization(
                baseline_mode,
                selected_ids,
                destination_exists=working_exists,
            )

            working_tree_buffer = (
                load_working_tree_file_as_buffer(
                    file_path,
                    **_spool_dir_options(spool_dir),
                )
                if working_tree_artifact_path is None
                else LineBuffer.from_path(
                    working_tree_artifact_path,
                    spool_dir=spool_dir,
                )
            )
            working_lines = stack.enter_context(working_tree_buffer)
            needs_trusted_target = bool(
                applied_presence_lines or index_preimage_presence_lines
            )
            if not needs_trusted_target:
                trusted_target_buffer = None
            elif captured_index_identity is None:
                trusted_target_buffer = read_git_object_buffer_or_none(
                    f":{file_path}",
                    **_spool_dir_options(spool_dir),
                )
            elif captured_index_identity.content_object_id is None:
                trusted_target_buffer = None
            else:
                trusted_target_buffer = load_git_blob_as_buffer(
                    captured_index_identity.content_object_id,
                    spool_dir=spool_dir,
                )
            trusted_target_lines = (
                None
                if trusted_target_buffer is None
                else stack.enter_context(trusted_target_buffer)
            )
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_discard,
                **_spool_dir_options(spool_dir),
            ) as ownership:
                if ownership.is_empty():
                    return DiscardTextPlanBuildResult()

                discard_options = (
                    {}
                    if not trusted_presence_lines
                    else {"trusted_presence_lines": trusted_presence_lines}
                )
                if applied_presence_lines:
                    discard_options["applied_presence_lines"] = applied_presence_lines
                discarded_buffer = discard_batch_from_line_sequences_as_buffer(
                    batch_source_lines,
                    ownership,
                    working_lines,
                    baseline_lines,
                    trusted_target_lines=trusted_target_lines,
                    index_preimage_presence_lines=(index_preimage_presence_lines),
                    **discard_options,
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
    except BaseException:
        if discarded_buffer is not None:
            discarded_buffer.close()
        raise


def _build_baseline_restore_text_plan(
    *,
    file_path: str,
    baseline_commit: str,
    spool_dir: str | Path | None = None,
) -> _action_plans.DiscardTextFileActionPlan:
    baseline_buffer = read_git_object_buffer_or_none(
        f"{baseline_commit}:{file_path}",
        **_spool_dir_options(spool_dir),
    )
    if baseline_buffer is None:
        return _action_plans.DiscardTextFileActionPlan(
            file_path,
            None,
            None,
            TextFileChangeType.DELETED,
        )
    try:
        return _action_plans.DiscardTextFileActionPlan(
            file_path,
            baseline_buffer,
            detect_file_mode_in_commit(baseline_commit, file_path),
            TextFileChangeType.MODIFIED,
        )
    except BaseException:
        baseline_buffer.close()
        raise
