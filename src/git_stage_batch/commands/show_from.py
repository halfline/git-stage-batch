"""Show from batch command implementation."""

from __future__ import annotations

import os
import shlex
import sys
from contextlib import ExitStack
from typing import Optional

from .selection import replacement_selection
from ..batch.atomic_file_changes import (
    binary_change_from_batch_file_metadata,
    gitlink_change_from_batch_file_metadata,
)
from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.operation_candidates import (
    OperationCandidatePreview,
    build_apply_candidate_previews,
    build_include_candidate_previews,
    render_candidate_buffer_diff,
    save_candidate_preview_state,
)
from ..batch.replacement import build_replacement_batch_view_from_lines
from ..core.replacement import (
    ReplacementPayload,
    coerce_replacement_payload,
)
from ..batch.selection import (
    acquire_batch_ownership_for_display_ids_from_lines,
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
)
from ..batch.source_selector import parse_batch_source_selector
from ..batch.submodule_pointer import is_batch_submodule_pointer
from ..batch.validation import batch_exists
from ..core.text_lifecycle import (
    mode_for_text_materialization,
    normalized_text_change_type,
)
from ..batch.file_display import render_batch_file_display
from ..data.batch_selected_changes import (
    compute_batch_binary_fingerprint,
    compute_batch_gitlink_fingerprint,
)
from ..data.selected_change.batch_file_cache import cache_rendered_batch_file_display
from ..data.file_review.batch_selection import translate_batch_file_gutter_ids_to_selection_ids
from ..data.selected_change.lifecycle import clear_selected_change_state_files
from ..data.selected_change.store import (
    SelectedChangeKind,
)
from ..data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_file_list,
)
from ..data.selected_change.file_changes import (
    cache_binary_file_change,
    cache_gitlink_change,
)
from ..data.file_review.records import FileReviewAction, ReviewSource
from ..data.file_review.state import (
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ..output.hunk import print_line_level_changes
from ..output.patch import (
    print_binary_file_change,
    print_gitlink_change,
)
from ..output.file_review import (
    build_file_review_model,
    make_file_review_state,
    normalize_page_spec,
    print_file_review,
    resolve_default_review_pages,
)
from ..output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    print_file_review_list,
)
from ..output.candidate_preview import (
    render_operation_candidate,
    render_operation_candidate_overview,
)
from ..core.buffer import LineBuffer
from ..utils.repository_buffers import (
    load_git_object_as_buffer,
    load_working_tree_file_as_buffer,
)
from ..exceptions import (
    exit_with_error,
    BatchMetadataError,
    CommandError,
    MergeError,
)
from ..i18n import _
from ..core.models import LineLevelChange
from ..utils.git import get_git_repository_root_path, require_git_repository
from ..utils.paths import get_context_lines


def _batch_source_args(batch_name: str) -> str:
    return f" --from {shlex.quote(batch_name)}"


def _shown_pages_for_display_ids(review_model, display_ids: set[int]) -> tuple[int, ...]:
    """Return review pages that contain the selected display IDs."""
    return tuple(
        sorted(
            {
                change.first_page
                for change in review_model.changes
                if set(change.display_ids) & display_ids
            }
        )
    )


def _resolve_candidate_ordinal(
    previews: tuple[OperationCandidatePreview, ...],
    *,
    explicit_ordinal: int,
) -> OperationCandidatePreview:
    if not previews:
        raise CommandError(_("No candidates available."))
    first = previews[0]
    ordinal = explicit_ordinal

    if ordinal > len(previews):
        raise CommandError(
            _("Batch '{batch}' has {count} {operation} candidates for {file}; candidate {ordinal} does not exist.").format(
                batch=first.batch_name,
                count=len(previews),
                operation=first.operation,
                file=first.file_path,
                ordinal=ordinal,
            )
        )
    if ordinal < 1:
        raise CommandError(_("Candidate ordinal must be at least 1."))
    return previews[ordinal - 1]


def _preview_replacement_batch_view(
    batch_name: str,
    metadata: dict,
    files: dict,
    line_ids: str,
    file_path: str,
    selected_ids: set[int],
    replacement_text: str | ReplacementPayload,
) -> None:
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary":
        exit_with_error(_("Cannot preview replacement text for binary files."))
    if is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Cannot preview replacement text for submodule pointers."))

    replacement_selection.require_contiguous_display_selection(selected_ids)
    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    with batch_source_buffer as batch_source_lines:
        selection_ids, _rendered = translate_batch_file_gutter_ids_to_selection_ids(
            batch_name,
            file_path,
            selected_ids,
            # Replacement preview is include-shaped because it previews include --from --as.
            FileReviewAction.INCLUDE_FROM_BATCH,
        )
        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids,
        ) as ownership:
            try:
                replacement_view = build_replacement_batch_view_from_lines(
                    batch_source_lines,
                    ownership,
                    coerce_replacement_payload(replacement_text),
                )
            except ValueError as e:
                exit_with_error(str(e))
            with replacement_view:
                before = LineBuffer.from_bytes(batch_source_buffer.to_bytes())
                try:
                    diff_text = render_candidate_buffer_diff(
                        file_path,
                        before,
                        replacement_view.source_buffer,
                        label_before="batch",
                        label_after="replacement-preview",
                        context_lines=get_context_lines(),
                    )
                    if diff_text:
                        print(diff_text, end="" if diff_text.endswith("\n") else "\n")
                finally:
                    before.close()


def _build_candidate_previews(
    *,
    selector,
    metadata: dict,
    files: dict,
    file_path: str,
    selected_ids: set[int] | None,
    replacement_text: str | ReplacementPayload | None,
) -> tuple[OperationCandidatePreview, ...]:
    file_meta = files[file_path]
    if file_meta.get("file_type") == "binary" or is_batch_submodule_pointer(file_meta):
        exit_with_error(_("Candidate preview is only available for text batch entries."))

    batch_source_commit = file_meta["batch_source_commit"]
    batch_source_buffer = load_git_object_as_buffer(f"{batch_source_commit}:{file_path}")
    if batch_source_buffer is None:
        exit_with_error(_("Batch source content is missing for {file}.").format(file=file_path))

    repo_root = get_git_repository_root_path()
    full_path = repo_root / file_path
    working_exists = os.path.lexists(full_path)
    text_change_type = normalized_text_change_type(file_meta.get("change_type"))
    batch_file_mode = str(file_meta.get("mode", "100644"))

    with batch_source_buffer as batch_source_lines:
        selection_ids_to_apply = selected_ids
        if selected_ids:
            action = (
                FileReviewAction.APPLY_FROM_BATCH
                if selector.candidate_operation == "apply"
                else FileReviewAction.INCLUDE_FROM_BATCH
            )
            selection_ids_to_apply, _rendered = translate_batch_file_gutter_ids_to_selection_ids(
                selector.batch_name,
                file_path,
                selected_ids,
                action,
            )

        with acquire_batch_ownership_for_display_ids_from_lines(
            file_meta,
            batch_source_lines,
            selection_ids_to_apply,
        ) as ownership:
            with ExitStack() as stack:
                source_for_candidates = batch_source_lines
                candidate_ownership = ownership
                replacement_payload = None
                if replacement_text is not None:
                    if selector.candidate_operation == "apply":
                        exit_with_error(_("Replacement preview is not valid for apply candidates."))
                    if not selected_ids:
                        exit_with_error(_("`show --from --as` requires `--line`."))
                    replacement_selection.require_contiguous_display_selection(
                        selected_ids,
                    )
                    replacement_payload = coerce_replacement_payload(replacement_text)
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

                if selector.candidate_operation == "apply":
                    worktree_file_mode = mode_for_text_materialization(
                        batch_file_mode,
                        selected_ids,
                        destination_exists=working_exists,
                    )
                    with load_working_tree_file_as_buffer(file_path) as working_lines:
                        return build_apply_candidate_previews(
                            batch_name=selector.batch_name,
                            file_path=file_path,
                            source_lines=source_for_candidates,
                            ownership=candidate_ownership,
                            worktree_lines=working_lines,
                            batch_source_commit=batch_source_commit,
                            file_meta=file_meta,
                            text_change_type=text_change_type,
                            worktree_file_mode=worktree_file_mode,
                            worktree_exists=working_exists,
                            selected_ids=selected_ids,
                            selection_ids=selection_ids_to_apply,
                        )

                index_buffer = load_git_object_as_buffer(f":{file_path}")
                index_exists = index_buffer is not None
                if index_buffer is None:
                    index_buffer = LineBuffer.from_bytes(b"")
                index_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=index_exists,
                )
                worktree_file_mode = mode_for_text_materialization(
                    batch_file_mode,
                    selected_ids,
                    destination_exists=working_exists,
                )
                with (
                    index_buffer as index_lines,
                    load_working_tree_file_as_buffer(file_path) as working_lines,
                ):
                    return build_include_candidate_previews(
                        batch_name=selector.batch_name,
                        file_path=file_path,
                        source_lines=source_for_candidates,
                        ownership=candidate_ownership,
                        index_lines=index_lines,
                        worktree_lines=working_lines,
                        batch_source_commit=batch_source_commit,
                        file_meta=file_meta,
                        text_change_type=text_change_type,
                        index_file_mode=index_file_mode,
                        worktree_file_mode=worktree_file_mode,
                        index_exists=index_exists,
                        worktree_exists=working_exists,
                        selected_ids=selected_ids,
                        selection_ids=selection_ids_to_apply,
                        replacement_payload=replacement_payload,
                    )


def command_show_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    selectable: bool = True,
    page: str | None = None,
    porcelain: bool = False,
    replacement_text: str | ReplacementPayload | None = None,
) -> None:
    """Show changes from a batch.

    Args:
        batch_name: Name of batch to show
        line_ids: Optional line IDs to filter (requires single-file context)
        file: Optional file path to show from batch.
              If None, shows all files in batch.
        patterns: Optional gitignore-style file patterns to filter batch files.
        selectable: If True, cache the displayed file for later line operations.
        page: Optional file-review page selection.
    """
    require_git_repository()
    selector = parse_batch_source_selector(batch_name)
    batch_name = selector.batch_name

    if selector.candidate_operation is not None and page is not None:
        exit_with_error(_("Candidate preview does not support --page."))

    # Check if batch exists
    if not batch_exists(batch_name):
        exit_with_error(_("Batch '{name}' does not exist").format(name=batch_name))

    # Read and validate batch metadata
    try:
        metadata = read_validated_batch_metadata(batch_name)
    except BatchMetadataError as e:
        exit_with_error(str(e))

    all_files = metadata.get("files", {})

    # Resolve file scope (for consistent --file handling across commands)
    files = resolve_batch_file_scope(batch_name, all_files, file, patterns)

    # Parse line selection and enforce single-file context
    selected_ids = require_single_file_context_for_line_selection(
        batch_name, files, line_ids, "show"
    )

    if selector.candidate_operation is not None:
        if patterns is not None or len(files) != 1:
            exit_with_error(_("Candidate preview requires exactly one file."))
        file_path = list(files.keys())[0]
        try:
            previews = _build_candidate_previews(
                selector=selector,
                metadata=metadata,
                files=files,
                file_path=file_path,
                selected_ids=selected_ids,
                replacement_text=replacement_text,
            )
        except ValueError as e:
            exit_with_error(str(e))
        except MergeError as e:
            exit_with_error(str(e))

        if not previews:
            exit_with_error(
                _("Batch '{batch}' has no {operation} candidates for {file}.").format(
                    batch=batch_name,
                    operation=selector.candidate_operation,
                    file=file_path,
                )
            )

        if selector.candidate_ordinal is None:
            try:
                reviewed_previews = render_operation_candidate_overview(
                    previews,
                    porcelain=porcelain,
                    note=metadata.get("note") or None,
                )
                for preview in reviewed_previews:
                    save_candidate_preview_state(preview)
            finally:
                for candidate in previews:
                    candidate.close()
            return

        preview = _resolve_candidate_ordinal(previews, explicit_ordinal=selector.candidate_ordinal)
        try:
            render_operation_candidate(
                preview,
                porcelain=porcelain,
                note=metadata.get("note") or None,
            )
            save_candidate_preview_state(preview)
        finally:
            for candidate in previews:
                candidate.close()
        return

    if porcelain:
        exit_with_error(_("--porcelain is only supported for candidate preview in `show --from`."))
    if replacement_text is not None:
        if not line_ids:
            exit_with_error(_("`show --from --as` requires `--line`."))
        if len(files) != 1:
            exit_with_error(_("`show --from --as` requires exactly one file."))
        file_path = list(files.keys())[0]
        _preview_replacement_batch_view(
            batch_name,
            metadata,
            files,
            line_ids,
            file_path,
            selected_ids,
            replacement_text,
        )
        return

    if len(files) == 1:
        # Show specific file from batch
        # Get the resolved file path
        file_path = list(files.keys())[0]
        binary_change = binary_change_from_batch_file_metadata(
            file_path,
            files[file_path],
        )
        if binary_change is not None:
            if selected_ids:
                exit_with_error(
                    _("Cannot use --lines with binary files. Run without --lines to view the binary change summary.")
                )
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_selected_change_state_files()
                cache_binary_file_change(
                    binary_change,
                    kind=SelectedChangeKind.BATCH_BINARY,
                    batch_name=batch_name,
                    batch_binary_fingerprint=compute_batch_binary_fingerprint(
                        batch_name,
                        file_path,
                        files[file_path],
                    ),
                )
            print_binary_file_change(binary_change)
            return

        gitlink_change = gitlink_change_from_batch_file_metadata(
            file_path,
            files[file_path],
        )
        if gitlink_change is not None:
            if selected_ids:
                exit_with_error(
                    _("Cannot use --lines with submodule pointers. Run without --lines to view the submodule pointer summary.")
                )
            if page is not None:
                exit_with_error(_("File review pages are only available for text changes."))
            if selectable:
                clear_selected_change_state_files()
                cache_gitlink_change(
                    gitlink_change,
                    kind=SelectedChangeKind.BATCH_GITLINK,
                    batch_name=batch_name,
                    batch_gitlink_fingerprint=compute_batch_gitlink_fingerprint(
                        file_path,
                        files[file_path],
                    ),
                )
            print_gitlink_change(gitlink_change)
            return

        rendered = render_batch_file_display(batch_name, file_path, metadata=metadata)
        if rendered is None:
            print(_("No changes for file '{file}' in batch '{name}'.").format(file=file_path, name=batch_name), file=sys.stderr)
            return

        review_model = None
        review_gutter_to_selection_id = (
            rendered.review_gutter_to_selection_id
            or rendered.gutter_to_selection_id
        )
        review_selection_id_to_gutter = (
            rendered.review_selection_id_to_gutter
            or rendered.selection_id_to_gutter
        )
        review_action_groups = rendered.review_action_groups or None

        def get_review_model():
            nonlocal review_model
            if review_model is None:
                review_model = build_file_review_model(
                    rendered.line_changes,
                    gutter_to_selection_id=review_gutter_to_selection_id,
                    actionable_selection_groups=rendered.actionable_selection_groups,
                    review_action_groups=review_action_groups,
                )
            return review_model

        if selectable and page is not None:
            resolve_default_review_pages(
                get_review_model(),
                requested_page_spec=page,
                previous_selection=None,
            )

        if page is not None or (selectable and not selected_ids):
            review_model = get_review_model()
            shown_pages = resolve_default_review_pages(
                review_model,
                requested_page_spec=page,
                previous_selection=None,
            )
            page_spec = normalize_page_spec(shown_pages, len(review_model.pages))
            if selectable:
                clear_last_file_review_state()
                cache_rendered_batch_file_display(file_path, rendered)
                write_last_file_review_state(
                    make_file_review_state(
                        review_model,
                        source=ReviewSource.BATCH,
                        batch_name=batch_name,
                        shown_pages=shown_pages,
                        selected_change_kind=SelectedChangeKind.BATCH_FILE,
                        gutter_to_selection_id=review_gutter_to_selection_id,
                        actionable_selection_groups=rendered.actionable_selection_groups,
                        review_action_groups=review_action_groups,
                    )
                )
            print_file_review(
                review_model,
                shown_pages=shown_pages,
                source_label=_("Changes: batch {name}").format(name=batch_name),
                page_spec=page_spec,
                command_source_args=_batch_source_args(batch_name),
                source=ReviewSource.BATCH,
                batch_name=batch_name,
                note=metadata.get("note") or None,
            )
            return

        # Filter by line IDs if specified (for display only)
        if selected_ids:
            line_gutter_to_selection_id = (
                review_gutter_to_selection_id
                if selectable else
                rendered.gutter_to_selection_id
            )

            # Translate gutter IDs (what user sees) to selection IDs (internal)
            selection_ids = set()
            for gutter_id in selected_ids:
                if gutter_id in line_gutter_to_selection_id:
                    selection_ids.add(line_gutter_to_selection_id[gutter_id])
                else:
                    exit_with_error(
                        _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                            id=gutter_id
                        )
                    )

            if selectable:
                clear_last_file_review_state()
                cache_rendered_batch_file_display(file_path, rendered)
                review_model = get_review_model()
                visible_review_display_ids = {
                    review_selection_id_to_gutter[selection_id]
                    for selection_id in selection_ids
                    if selection_id in review_selection_id_to_gutter
                }
                shown_pages = _shown_pages_for_display_ids(review_model, visible_review_display_ids)
                if shown_pages:
                    write_last_file_review_state(
                        make_file_review_state(
                            review_model,
                            source=ReviewSource.BATCH,
                            batch_name=batch_name,
                            shown_pages=shown_pages,
                            selected_change_kind=SelectedChangeKind.BATCH_FILE,
                            gutter_to_selection_id=review_gutter_to_selection_id,
                            actionable_selection_groups=rendered.actionable_selection_groups,
                            review_action_groups=review_action_groups,
                            visible_display_ids=visible_review_display_ids,
                            entire_file_shown=False,
                        )
                    )

            # Filter by selection IDs (not gutter IDs)
            filtered_lines = [line for line in rendered.line_changes.lines if line.id in selection_ids]
            if filtered_lines:
                filtered_line_changes = LineLevelChange(
                    path=rendered.line_changes.path,
                    lines=filtered_lines,
                    header=rendered.line_changes.header
                )
                print_line_level_changes(filtered_line_changes, gutter_to_selection_id=line_gutter_to_selection_id)
        else:
            print_line_level_changes(
                    rendered.line_changes,
                    gutter_to_selection_id=(
                        review_gutter_to_selection_id
                        if selectable else
                        {}
                    ),
                )

        return

    entries = []
    for file_path, file_meta in files.items():
        binary_change = binary_change_from_batch_file_metadata(file_path, file_meta)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = gitlink_change_from_batch_file_metadata(file_path, file_meta)
        if gitlink_change is not None:
            entries.append(make_gitlink_file_review_list_entry(gitlink_change))
            continue
        rendered = render_batch_file_display(
            batch_name,
            file_path,
            metadata=metadata,
            probe_mergeability=False,
        )
        if rendered is not None:
            entries.append(
                make_file_review_list_entry(
                    rendered.line_changes,
                )
            )

    if entries:
        # Multi-file batch output is navigational; it must not leave a hidden
        # selected file that a later bare action could operate on.
        if selectable:
            clear_selected_change_state_files()
            mark_selected_change_cleared_by_file_list(
                source=ReviewSource.BATCH.value,
                batch_name=batch_name,
            )
        print_file_review_list(
            source_label=_("Changes: batch {name}").format(name=batch_name),
            entries=entries,
            command_source_args=_batch_source_args(batch_name),
        )
    else:
        print(_("Batch '{name}' is empty").format(name=batch_name), file=sys.stderr)
