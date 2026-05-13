"""Show from batch command implementation."""

from __future__ import annotations

import shlex
import sys
from typing import Optional

from ..batch.metadata_validation import read_validated_batch_metadata
from ..batch.selection import (
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
)
from ..batch.validation import batch_exists
from ..data.hunk_tracking import (
    SelectedChangeKind,
    cache_binary_file_change,
    cache_gitlink_change,
    cache_rendered_batch_file_display,
    clear_selected_change_state_files,
    compute_batch_binary_fingerprint,
    compute_batch_gitlink_fingerprint,
    mark_selected_change_cleared_by_file_list,
    render_batch_file_display,
)
from ..data.file_review_state import (
    ReviewSource,
    clear_last_file_review_state,
    write_last_file_review_state,
)
from ..output import print_binary_file_change, print_gitlink_change, print_line_level_changes
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
from ..exceptions import exit_with_error, BatchMetadataError
from ..i18n import _
from ..core.models import BinaryFileChange, GitlinkChange, LineLevelChange
from ..utils.git import require_git_repository


def _batch_source_args(batch_name: str) -> str:
    return f" --from {shlex.quote(batch_name)}"


def _render_batch_binary_file_change(file_path: str, file_meta: dict) -> BinaryFileChange | None:
    """Return an atomic binary batch change for display, if the entry is binary."""
    if file_meta.get("file_type") != "binary":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return BinaryFileChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        change_type=change_type,
    )


def _render_batch_gitlink_change(file_path: str, file_meta: dict) -> GitlinkChange | None:
    """Return an atomic submodule pointer batch change, if the entry is one."""
    if file_meta.get("file_type") != "gitlink":
        return None
    change_type = file_meta.get("change_type")
    if change_type not in ("added", "modified", "deleted"):
        return None
    return GitlinkChange(
        old_path="/dev/null" if change_type == "added" else file_path,
        new_path="/dev/null" if change_type == "deleted" else file_path,
        old_oid=file_meta.get("old_oid"),
        new_oid=file_meta.get("new_oid"),
        change_type=change_type,
    )


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


def command_show_from_batch(
    batch_name: str,
    line_ids: Optional[str] = None,
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    selectable: bool = True,
    page: str | None = None,
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

    if len(files) == 1:
        # Show specific file from batch
        # Get the resolved file path
        file_path = list(files.keys())[0]
        binary_change = _render_batch_binary_file_change(file_path, files[file_path])
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

        gitlink_change = _render_batch_gitlink_change(file_path, files[file_path])
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
        binary_change = _render_batch_binary_file_change(file_path, file_meta)
        if binary_change is not None:
            entries.append(make_binary_file_review_list_entry(binary_change))
            continue
        gitlink_change = _render_batch_gitlink_change(file_path, file_meta)
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
