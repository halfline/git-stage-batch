"""Show from batch command implementation."""

from __future__ import annotations

import shlex
import sys
from typing import Optional

from .batch_source import candidate_preview_action as _candidate_preview_action
from .batch_source import file_display_action as _file_display_action
from .batch_source import replacement_previews as _replacement_previews
from ..batch.atomic_file_changes import (
    binary_change_from_batch_file_metadata,
    gitlink_change_from_batch_file_metadata,
)
from ..batch.metadata_validation import read_validated_batch_metadata
from ..core.replacement import ReplacementPayload
from ..batch.selection import (
    resolve_batch_file_scope,
    require_single_file_context_for_line_selection,
)
from ..batch.source_selector import parse_batch_source_selector
from ..batch.validation import batch_exists
from ..batch.file_display import render_batch_file_display
from ..data.file_review.batch_selection import translate_batch_file_gutter_ids_to_selection_ids
from ..data.selected_change.lifecycle import clear_selected_change_state_files
from ..data.selected_change.clear_reasons import (
    mark_selected_change_cleared_by_file_list,
)
from ..data.file_review.records import ReviewSource
from ..output.file_review_list import (
    make_binary_file_review_list_entry,
    make_file_review_list_entry,
    make_gitlink_file_review_list_entry,
    print_file_review_list,
)
from ..exceptions import (
    exit_with_error,
    BatchMetadataError,
)
from ..i18n import _
from ..utils.git_repository import require_git_repository


def _batch_source_args(batch_name: str) -> str:
    return f" --from {shlex.quote(batch_name)}"


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
        _candidate_preview_action.show_batch_source_candidate_preview(
            selector=selector,
            batch_name=batch_name,
            files=files,
            selected_ids=selected_ids,
            replacement_text=replacement_text,
            patterns=patterns,
            porcelain=porcelain,
            note=metadata.get("note") or None,
        )
        return

    if porcelain:
        exit_with_error(_("--porcelain is only supported for candidate preview in `show --from`."))
    if replacement_text is not None:
        if not line_ids:
            exit_with_error(_("`show --from --as` requires `--line`."))
        if len(files) != 1:
            exit_with_error(_("`show --from --as` requires exactly one file."))
        file_path = list(files.keys())[0]
        _replacement_previews.print_batch_source_replacement_preview(
            batch_name=batch_name,
            files=files,
            file_path=file_path,
            selected_ids=selected_ids,
            replacement_text=replacement_text,
            translate_selection_ids=(
                translate_batch_file_gutter_ids_to_selection_ids
            ),
        )
        return

    if len(files) == 1:
        file_path = list(files.keys())[0]
        _file_display_action.show_batch_source_file_display(
            batch_name=batch_name,
            file_path=file_path,
            files=files,
            metadata=metadata,
            selected_ids=selected_ids,
            selectable=selectable,
            page=page,
            command_source_args=_batch_source_args(batch_name),
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
