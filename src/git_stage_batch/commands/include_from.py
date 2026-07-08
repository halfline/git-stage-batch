"""Include from batch command implementation."""

from __future__ import annotations

from contextlib import ExitStack
import os
import sys
from typing import Optional

from .batch_source import action_plans as _action_plans
from .batch_source import binary_file_actions as _binary_file_actions
from .batch_source import candidate_previews as _candidate_previews
from .batch_source import text_file_actions as _text_file_actions
from .selection import replacement_selection
from ..batch.binary_file_content import read_binary_file_from_batch
from ..batch.merge import merge_batch_from_line_sequences_as_buffer
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operation_candidates import (
    CandidateEnumerationLimitError,
    CandidatePreviewCount,
    build_include_candidate_previews,
    clear_candidate_preview_state_for_file,
)
from ..batch.replacement import build_replacement_batch_view_from_lines
from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..batch.selection import (
    acquire_batch_ownership_for_display_ids_from_lines,
    resolve_current_batch_binary_file_scope,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
    translate_atomic_unit_error_to_gutter_ids,
)
from ..batch.submodule_pointer import (
    is_batch_submodule_pointer,
    refuse_batch_submodule_pointer_lines,
    stage_submodule_pointer_from_batch,
)
from ..batch.source_selector import (
    parse_batch_source_selector,
    require_candidate_operation,
)
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    TextFileChangeType,
    mode_for_text_materialization,
    normalized_text_change_type,
    selected_text_target_change_type,
)
from ..data.file_review.records import FileReviewAction
from ..data.file_review.state import (
    finish_review_scoped_line_action,
    resolve_batch_source_action_scope,
)
from ..data.file_review.batch_selection import translate_batch_file_gutter_ids_to_selection_ids
from ..batch.file_display import render_batch_file_display
from ..core.buffer import LineBuffer
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..data.session import snapshot_file_if_untracked
from ..data.undo import undo_checkpoint
from ..exceptions import (
    AtomicUnitError,
    BatchMetadataError,
    CommandError,
    MergeError,
    exit_with_error,
)
from ..i18n import _
from ..utils.git import (
    get_git_repository_root_path,
    git_refresh_index,
    require_git_repository,
)


def _execute_include_candidate(
    *,
    batch_name: str,
    raw_selector: str,
    ordinal: int,
    files: dict,
    selected_ids: set[int] | None,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
) -> None:
    """Recompute and include one previewed include candidate."""
    if len(files) != 1:
        exit_with_error(_("Candidate execution requires exactly one file."))
    file_path, file_meta = next(iter(files.items()))
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Candidate execution is only available for text batch entries."))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))
    index_buffer = load_git_object_as_buffer(f":{file_path}")
    index_exists = index_buffer is not None
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"")
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

    with (
        batch_source_buffer as batch_source_lines,
        index_buffer as index_lines,
        load_working_tree_file_as_buffer(file_path) as working_lines,
    ):
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_include,
        ) as ownership:
            with ExitStack() as stack:
                source_for_candidates = batch_source_lines
                candidate_ownership = ownership
                if replacement_payload is not None:
                    try:
                        replacement_view = build_replacement_batch_view_from_lines(
                            batch_source_lines,
                            ownership,
                            replacement_payload,
                        )
                    except ValueError as e:
                        exit_with_error(str(e))
                    replacement_view = stack.enter_context(replacement_view)
                    source_for_candidates = replacement_view.source_buffer
                    candidate_ownership = replacement_view.ownership
                try:
                    previews = build_include_candidate_previews(
                        batch_name=batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=working_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
                        selected_ids=selected_ids,
                        selection_ids=selection_ids_to_include,
                        replacement_payload=replacement_payload,
                    )
                except (MergeError, ValueError) as e:
                    exit_with_error(str(e))

            preview = _candidate_previews.candidate_preview_for_ordinal(
                previews,
                ordinal,
            )
            if preview is None:
                _candidate_previews.close_candidate_previews(previews)
                exit_with_error(
                    _("Batch '{batch}' has {count} include candidates for {file}; candidate {ordinal} does not exist.").format(
                        batch=batch_name,
                        count=len(previews),
                        file=file_path,
                        ordinal=ordinal,
                    )
                )

            try:
                if not _candidate_previews.candidate_preview_state_matches(
                    preview,
                    ordinal,
                ):
                    exit_with_error(
                        _(
                            "Candidate selector '{selector}' has not been previewed for {file}.\n"
                            "No changes applied.\n\n"
                            "Preview it first with:\n"
                            "  git-stage-batch show --from {selector} --file {file}"
                        ).format(selector=raw_selector, file=file_path)
                    )

                index_target = next(target for target in preview.targets if target.target == "index")
                worktree_target = next(target for target in preview.targets if target.target == "worktree")
                index_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    index_target.after_buffer,
                )
                working_change_type = selected_text_target_change_type(
                    text_change_type,
                    selected_ids,
                    worktree_target.after_buffer,
                )
                print(
                    _("Including candidate {ordinal} of {count} for batch '{batch}':").format(
                        ordinal=preview.ordinal,
                        count=preview.count,
                        batch=batch_name,
                    ),
                    file=sys.stderr,
                )
                print(f"  {file_path}:", file=sys.stderr)
                print(f"    {_('Index')}", file=sys.stderr)
                print(f"    {_('Working tree')}", file=sys.stderr)
                operation_parts = ["include", "--from", raw_selector, "--file", file_path]
                with undo_checkpoint(" ".join(operation_parts), worktree_paths=[file_path]):
                    snapshot_file_if_untracked(file_path)
                    _text_file_actions.stage_text_file_to_index(
                        file_path,
                        index_target.after_buffer,
                        index_file_mode,
                        index_change_type,
                    )
                    _text_file_actions.write_text_file_to_worktree(
                        file_path,
                        worktree_target.after_buffer,
                        working_file_mode,
                        working_change_type,
                    )
                clear_candidate_preview_state_for_file(
                    batch_name=batch_name,
                    file_path=file_path,
                )
                print(
                    _("✓ Included candidate {ordinal} of {count} from batch '{batch}'").format(
                        ordinal=preview.ordinal,
                        count=preview.count,
                        batch=batch_name,
                    ),
                    file=sys.stderr,
                )
            finally:
                _candidate_previews.close_candidate_previews(previews)


def _include_candidate_count_for_file(
    *,
    batch_name: str,
    file_path: str,
    file_meta: dict,
    selection_ids_to_include: set[int] | None,
    replacement_payload: ReplacementPayload | None,
) -> CandidatePreviewCount:
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        return CandidatePreviewCount()
    batch_source_commit = file_meta.get("batch_source_commit")
    if not batch_source_commit:
        return CandidatePreviewCount()
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        return CandidatePreviewCount()
    index_buffer = load_git_object_as_buffer(f":{file_path}")
    index_exists = index_buffer is not None
    if index_buffer is None:
        index_buffer = LineBuffer.from_bytes(b"")
    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))
    index_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selection_ids_to_include,
        destination_exists=index_exists,
    )
    working_file_mode = mode_for_text_materialization(
        batch_file_mode,
        selection_ids_to_include,
        destination_exists=working_exists,
    )
    try:
        with (
            batch_source_buffer as batch_source_lines,
            index_buffer as index_lines,
            load_working_tree_file_as_buffer(file_path) as working_lines,
        ):
            with acquire_batch_ownership_for_display_ids_from_lines(
                file_meta,
                batch_source_lines,
                selection_ids_to_include,
            ) as ownership:
                with ExitStack() as stack:
                    source_for_candidates = batch_source_lines
                    candidate_ownership = ownership
                    if replacement_payload is not None:
                        replacement_view = build_replacement_batch_view_from_lines(
                            batch_source_lines,
                            ownership,
                            replacement_payload,
                        )
                        replacement_view = stack.enter_context(replacement_view)
                        source_for_candidates = replacement_view.source_buffer
                        candidate_ownership = replacement_view.ownership
                    previews = build_include_candidate_previews(
                        batch_name=batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=working_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
                        selected_ids=selection_ids_to_include,
                        selection_ids=selection_ids_to_include,
                        replacement_payload=replacement_payload,
                    )
                count = len(previews)
                _candidate_previews.close_candidate_previews(previews)
                return CandidatePreviewCount(count=count)
    except CandidateEnumerationLimitError as e:
        return CandidatePreviewCount(too_many=True, error=str(e))
    except MergeError:
        return CandidatePreviewCount()
    except Exception as e:
        return CandidatePreviewCount(error=str(e))


def command_include_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    replacement_text: Optional[str | ReplacementPayload] = None,
) -> None:
    """Stage batch changes to index and working tree using structural merge.

    Args:
        batch_name: Name of batch to include from
        line_ids: Optional line IDs to include (requires single-file context)
        file: Optional file path to select from batch.
              If None, includes all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        replacement_text: Optional replacement text for selected batch lines.
    """
    require_git_repository()
    raw_selector = batch_name
    selector = parse_batch_source_selector(batch_name)
    require_candidate_operation(selector, "include", raw_value=raw_selector, file=file)
    if selector.candidate_operation == "include" and selector.candidate_ordinal is None:
        exit_with_error(
            _(
                "'{selector}' names the include candidate preview set.\n"
                "Use 'git-stage-batch show --from {selector}' to preview candidates, "
                "or use '{batch}:include:N' to include a candidate."
            ).format(selector=raw_selector, batch=selector.batch_name)
        )
    if selector.candidate_ordinal is not None and file is None:
        exit_with_error(
            _(
                "Candidate selector '{selector}' requires --file in this implementation.\n"
                "No changes applied."
            ).format(selector=raw_selector)
        )
    batch_name = selector.batch_name
    scope_resolution = resolve_batch_source_action_scope(
        FileReviewAction.INCLUDE_FROM_BATCH,
        command_name="include",
        batch_name=batch_name,
        line_ids=line_ids,
        file=file,
        patterns=patterns,
    )
    file = scope_resolution.file

    # Refresh index to ensure git's cached stat info is up-to-date
    git_refresh_index(check=False)

    # Check batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    if not all_files:
        exit_with_error(_("Batch '{name}' is empty").format(name=batch_name))

    file = resolve_current_batch_binary_file_scope(batch_name, all_files, file, patterns, line_ids)

    # Determine which files to operate on
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "include"
    )
    replacement_payload = (
        coerce_replacement_payload(replacement_text)
        if replacement_text is not None
        else None
    )

    if replacement_payload is not None and not selected_ids:
        exit_with_error(_("`include --from --as` requires `--line`."))

    if selected_ids:
        file_path_for_check = list(files.keys())[0]  # Single file context enforced above
        if files[file_path_for_check].get("file_type") == "binary":
            exit_with_error(_("Cannot use --lines with binary files. Include the whole file instead."))
        if is_batch_submodule_pointer(files[file_path_for_check]):
            refuse_batch_submodule_pointer_lines(_("Include"))

    # Translate gutter IDs to selection IDs if line selection is active
    selection_ids_to_include = selected_ids
    rendered = None  # Store for error translation
    if selected_ids:
        if replacement_payload is not None:
            replacement_selection.require_contiguous_display_selection(selected_ids)
        file_path_for_render = list(files.keys())[0]  # Single file context enforced above
        selection_ids_to_include, rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path_for_render,
            selected_ids,
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
    operation_parts = ["include", "--from", raw_selector]
    if line_ids is not None:
        operation_parts.extend(["--line", line_ids])
    if file is not None:
        operation_parts.extend(["--file", file])
    if replacement_payload is not None:
        operation_parts.extend(["--as", replacement_payload.display_text or "<stdin>"])
    if selector.candidate_ordinal is not None:
        _execute_include_candidate(
            batch_name=batch_name,
            raw_selector=raw_selector,
            ordinal=selector.candidate_ordinal,
            files=files,
            selected_ids=selected_ids,
            selection_ids_to_include=selection_ids_to_include,
            replacement_payload=replacement_payload,
        )
        return

    repo_root = get_git_repository_root_path()
    failed_files = []
    candidate_counts: dict[str, CandidatePreviewCount] = {}
    include_plans = []

    for file_path, file_meta in files.items():
        merged_index_buffer = None
        merged_working_buffer = None
        try:
            if file_meta.get("file_type") == "binary":
                batch_buffer = read_binary_file_from_batch(
                    batch_name,
                    file_path,
                    file_meta,
                    missing_content_message=(
                        f"Binary file not found in batch commit: {file_path}"
                    ),
                )
                include_plans.append(
                    _action_plans.BinaryFileActionPlan(
                        file_path,
                        file_meta,
                        batch_buffer,
                    )
                )
                continue
            if is_batch_submodule_pointer(file_meta):
                include_plans.append(
                    _action_plans.SubmodulePointerActionPlan(file_path, file_meta)
                )
                continue

            text_change_type = normalized_text_change_type(file_meta.get("change_type"))

            index_buffer = load_git_object_as_buffer(f":{file_path}")
            index_exists = index_buffer is not None
            if index_buffer is None:
                index_buffer = LineBuffer.from_bytes(b"")

            full_path = repo_root / file_path
            working_exists = os.path.lexists(full_path)

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
            if selected_ids is None and text_change_type == TextFileChangeType.DELETED:
                index_buffer.close()
                include_plans.append(
                    _action_plans.IncludeTextFileActionPlan(
                        file_path,
                        None,
                        None,
                        index_file_mode,
                        working_file_mode,
                        text_change_type,
                        text_change_type,
                    )
                )
                continue

            batch_source_commit = file_meta["batch_source_commit"]
            batch_source_buffer = load_git_object_as_buffer(
                f"{batch_source_commit}:{file_path}"
            )
            if batch_source_buffer is None:
                index_buffer.close()
                failed_files.append(file_path)
                continue

            with (
                batch_source_buffer as batch_source_lines,
                index_buffer as index_lines,
                load_working_tree_file_as_buffer(file_path) as working_lines,
            ):
                try:
                    with acquire_batch_ownership_for_display_ids_from_lines(
                        file_meta,
                        batch_source_lines,
                        selection_ids_to_include,
                    ) as ownership:
                        if ownership.is_empty():
                            if selected_ids is None and text_change_type == TextFileChangeType.ADDED:
                                merged_index_buffer = LineBuffer.from_bytes(b"")
                                merged_working_buffer = LineBuffer.from_bytes(b"")
                            else:
                                continue
                        elif replacement_payload is not None:
                            try:
                                replacement_view = build_replacement_batch_view_from_lines(
                                    batch_source_lines,
                                    ownership,
                                    replacement_payload,
                                )
                            except ValueError as e:
                                _action_plans.close_action_plans(include_plans)
                                exit_with_error(str(e))

                            with replacement_view:
                                ownership = replacement_view.ownership
                                merged_index_buffer = merge_batch_from_line_sequences_as_buffer(
                                    replacement_view.source_buffer,
                                    ownership,
                                    index_lines,
                                )
                                merged_working_buffer = merge_batch_from_line_sequences_as_buffer(
                                    replacement_view.source_buffer,
                                    ownership,
                                    working_lines,
                                )
                        else:
                            merged_index_buffer = merge_batch_from_line_sequences_as_buffer(
                                batch_source_lines,
                                ownership,
                                index_lines,
                            )
                            merged_working_buffer = merge_batch_from_line_sequences_as_buffer(
                                batch_source_lines,
                                ownership,
                                working_lines,
                            )
                except AtomicUnitError as e:
                    if rendered:
                        translate_atomic_unit_error_to_gutter_ids(e, rendered, "include from", batch_name)
                    _action_plans.close_action_plans(include_plans)
                    exit_with_error(_("Failed to include from batch '{name}': {error}").format(
                        name=batch_name,
                        error=str(e)
                    ))

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
            include_plans.append(
                _action_plans.IncludeTextFileActionPlan(
                    file_path,
                    merged_index_buffer,
                    merged_working_buffer,
                    index_file_mode,
                    working_file_mode,
                    index_change_type,
                    working_change_type,
                )
            )
            merged_index_buffer = None
            merged_working_buffer = None

        except MergeError:
            if merged_index_buffer is not None:
                merged_index_buffer.close()
            if merged_working_buffer is not None:
                merged_working_buffer.close()
            # Merge conflict - batch created from different file version
            candidate_count = _include_candidate_count_for_file(
                batch_name=batch_name,
                file_path=file_path,
                file_meta=file_meta,
                selection_ids_to_include=selection_ids_to_include,
                replacement_payload=replacement_payload,
            )
            if candidate_count.count or candidate_count.too_many or candidate_count.error:
                candidate_counts[file_path] = candidate_count
            failed_files.append(file_path)
        except CommandError:
            # Re-raise user errors (e.g., partial atomic selection)
            _action_plans.close_action_plans(include_plans)
            raise
        except Exception as e:
            print(_("Error staging {file}: {error}").format(file=file_path, error=str(e)), file=sys.stderr)
            failed_files.append(file_path)

    if failed_files:
        _action_plans.close_action_plans(include_plans)
        candidate_limit_files = [
            file_path
            for file_path in failed_files
            if candidate_counts.get(file_path, CandidatePreviewCount()).too_many
        ]
        if len(candidate_limit_files) == 1:
            file_path = candidate_limit_files[0]
            exit_with_error(
                _(
                    "Cannot include batch '{batch}': {file} has too many include "
                    "candidates to preview safely.\n"
                    "No changes applied.\n\n"
                    "Use --line with a narrower selection or split the batch "
                    "before previewing candidates."
                ).format(batch=batch_name, file=file_path)
            )
        if len(candidate_limit_files) > 1:
            exit_with_error(
                _(
                    "Cannot include batch '{batch}': multiple files have too many "
                    "include candidates to preview safely.\n"
                    "No changes applied.\n\n"
                    "Use --line with narrower selections or split the batch "
                    "before previewing candidates."
                ).format(batch=batch_name)
            )

        candidate_error_files = [
            file_path
            for file_path in failed_files
            if (
                candidate_counts.get(file_path, CandidatePreviewCount()).error
                and not candidate_counts.get(file_path, CandidatePreviewCount()).too_many
            )
        ]
        if len(candidate_error_files) == 1:
            file_path = candidate_error_files[0]
            error = candidate_counts[file_path].error
            exit_with_error(
                _(
                    "Cannot enumerate include candidates for {file}: {error}\n"
                    "No changes applied."
                ).format(file=file_path, error=error)
            )
        if len(candidate_error_files) > 1:
            examples = "\n".join(
                f"  {file_path}: {candidate_counts[file_path].error}"
                for file_path in candidate_error_files[:3]
            )
            exit_with_error(
                _(
                    "Cannot enumerate include candidates for multiple files.\n"
                    "No changes applied.\n\n"
                    "{examples}"
                ).format(examples=examples)
            )

        ambiguous_files = [
            file_path
            for file_path in failed_files
            if candidate_counts.get(file_path, CandidatePreviewCount()).count
        ]
        if len(ambiguous_files) == 1:
            file_path = ambiguous_files[0]
            exit_with_error(
                _(
                    "Cannot include batch '{batch}': {file} has {count} include candidates.\n"
                    "No changes applied.\n\n"
                    "Preview candidates:\n"
                    "  git-stage-batch show --from {batch}:include --file {file}\n\n"
                    "Include a reviewed candidate:\n"
                    "  git-stage-batch include --from {batch}:include:N --file {file}"
                ).format(
                    batch=batch_name,
                    file=file_path,
                    count=candidate_counts[file_path].count,
                )
            )
        if len(ambiguous_files) > 1:
            examples = "\n".join(
                f"  git-stage-batch show --from {batch_name}:include --file {file_path}"
                for file_path in ambiguous_files[:3]
            )
            exit_with_error(
                _(
                    "Cannot include batch '{batch}': multiple files need include decisions.\n"
                    "No changes applied.\n\n"
                    "Resolve one file at a time:\n{examples}"
                ).format(batch=batch_name, examples=examples)
            )
        if len(failed_files) == 1:
            # Check if there are individually mergeable lines to suggest --lines
            file_path = failed_files[0]
            rendered = render_batch_file_display(batch_name, file_path)
            has_mergeable_lines = rendered and len(rendered.gutter_to_selection_id) > 0

            if has_mergeable_lines:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch, "
                             "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    file=file_path
                )
            else:
                error_msg = _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                             "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                    batch=batch_name,
                    file=file_path
                )
            exit_with_error(error_msg)
        else:
            exit_with_error(
                _("Batch '{batch}' contains changes to one or more files that are incompatible with the current working tree. "
                  "Failed for: {files}. "
                  "Use 'git-stage-batch show --from {batch}' to review the batch, "
                  "or use '--lines' to apply only specific changes.").format(
                    batch=batch_name,
                    files=', '.join(failed_files)
                )
            )

    try:
        try:
            with undo_checkpoint(" ".join(operation_parts), worktree_paths=list(files)):
                for plan in include_plans:
                    snapshot_file_if_untracked(plan.file_path)
                    if isinstance(plan, _action_plans.IncludeTextFileActionPlan):
                        _text_file_actions.stage_text_file_to_index(
                            plan.file_path,
                            plan.index_buffer,
                            plan.index_file_mode,
                            plan.index_change_type,
                        )
                        _text_file_actions.write_text_file_to_worktree(
                            plan.file_path,
                            plan.working_buffer,
                            plan.working_file_mode,
                            plan.working_change_type,
                        )
                    elif isinstance(plan, _action_plans.BinaryFileActionPlan):
                        _binary_file_actions.stage_binary_file_to_index(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                        )
                        _binary_file_actions.write_binary_file_to_worktree(
                            plan.file_path,
                            plan.file_meta,
                            plan.buffer,
                        )
                    else:
                        stage_submodule_pointer_from_batch(plan.file_path, plan.file_meta)
        except CommandError:
            raise
        except Exception:
            if len(files) == 1:
                file_path = next(iter(files))
                exit_with_error(
                    _("Batch '{batch}' contains changes to {file} that are incompatible with the current working tree. "
                      "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                        batch=batch_name,
                        file=file_path,
                    )
                )
            exit_with_error(
                _("Batch '{batch}' contains changes to one or more files that are incompatible with the current working tree. "
                  "Use 'git-stage-batch show --from {batch}' to review the batch.").format(
                    batch=batch_name,
                )
            )
    finally:
        _action_plans.close_action_plans(include_plans)

    for file_path in files:
        finish_review_scoped_line_action(scope_resolution.review_state, file_path=file_path)

    if replacement_payload is not None and line_ids:
        print(
            _("✓ Staged selected lines as replacement from batch '{name}'").format(name=batch_name),
            file=sys.stderr,
        )
    elif line_ids:
        print(_("✓ Staged selected lines from batch '{name}'").format(name=batch_name), file=sys.stderr)
    elif file is not None:
        print(_("✓ Staged changes for {file} from batch '{name}'").format(file=list(files.keys())[0], name=batch_name), file=sys.stderr)
    else:
        print(_("✓ Staged changes from batch '{name}'").format(name=batch_name), file=sys.stderr)
