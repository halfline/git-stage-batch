"""Show from batch command implementation."""

from __future__ import annotations

import shlex
from typing import Optional

from .batch_source import candidate_preview_action as _candidate_preview_action
from .batch_source import file_display_action as _file_display_action
from .batch_source import file_list_action as _file_list_action
from .batch_source import replacement_previews as _replacement_previews
from ..batch.state.validation import read_validated_batch_metadata
from ..core.replacement import ReplacementPayload
from ..batch.selection import require_single_file_context_for_line_selection
from ..batch.source.selector import parse_batch_source_selector
from ..batch.state.batch_names import batch_exists
from ..data.batch_file_scope import resolve_batch_file_scope
from ..data.file_review.batch_selection import translate_batch_file_gutter_ids_to_selection_ids
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

    _file_list_action.show_batch_source_file_list(
        batch_name=batch_name,
        files=files,
        metadata=metadata,
        selectable=selectable,
        command_source_args=_batch_source_args(batch_name),
    )
