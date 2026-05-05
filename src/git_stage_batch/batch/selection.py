"""Shared batch selection and filtering logic for commands."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Optional

from .ownership import (
    BatchOwnership,
    build_ownership_units_from_display,
    select_ownership_units_by_display_ids,
    validate_ownership_units,
    rebuild_ownership_from_units,
)
from ..core.line_selection import parse_line_selection
from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns

if TYPE_CHECKING:
    from ..exceptions import AtomicUnitError
    from ..core.models import RenderedBatchDisplay
    from ..data.file_review_state import FileReviewAction


def resolve_batch_file_scope(
    batch_name: str,
    all_files: dict[str, dict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
) -> dict[str, dict]:
    """Resolve which files from a batch to operate on.

    Args:
        batch_name: Name of the batch
        all_files: All files in the batch metadata
        file: Optional file path filter:
            - None: operate on all files in batch
            - "": use currently selected hunk's file
            - path: specific file path
        patterns: Optional gitignore-style file patterns to resolve against batch files

    Returns:
        Dictionary of file paths to file metadata for selected files

    Raises:
        SystemExit: If file not found or no hunk selected when using ""
    """
    if file is not None:
        # Specific file requested
        from ..data.hunk_tracking import get_batch_file_for_line_operation, get_selected_change_file_path

        # If file is empty string, use selected hunk's file
        if file == "":
            file_to_use = get_selected_change_file_path()
            if file_to_use is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            file_to_use = file

        target_file = get_batch_file_for_line_operation(batch_name, file_to_use)
        return {target_file: all_files[target_file]}
    if patterns is not None:
        resolved_files = resolve_gitignore_style_patterns(all_files.keys(), patterns)
        if not resolved_files:
            exit_with_error(
                _("No files in batch '{name}' matched: {patterns}").format(
                    name=batch_name,
                    patterns=", ".join(patterns),
                )
            )
        return {file_path: all_files[file_path] for file_path in resolved_files}
    else:
        # All files in batch (default)
        return all_files


def require_single_file_context_for_line_selection(
    batch_name: str,
    files: dict[str, dict],
    line_ids: Optional[str],
    operation_verb: str,
) -> Optional[set[int]]:
    """Parse line IDs and enforce single-file context requirement.

    Line-level operations require single-file context to avoid ambiguous
    line ID interpretation across multiple files.

    Args:
        batch_name: Name of the batch
        files: Files in scope for the operation
        line_ids: Optional line selection string (e.g., "1,2,3" or "1-5")
        operation_verb: Operation name for error message (e.g., "apply", "include")

    Returns:
        Set of selected line IDs if line_ids provided, otherwise None

    Raises:
        SystemExit: If line_ids provided but multiple files in scope
    """
    if line_ids is None:
        return None

    # Line-level operation requires single-file context
    if len(files) != 1:
        exit_with_error(
            _("Line-level {operation} (--line) requires single-file context.\n"
              "Use --file to specify a file, or run 'show --from {name}' first to select a file.").format(
                operation=operation_verb,
                name=batch_name
            )
        )

    return set(parse_line_selection(line_ids))


def translate_atomic_unit_error_to_gutter_ids(
    error: 'AtomicUnitError',
    rendered: 'RenderedBatchDisplay',
    operation_verb: str,
    batch_name: str
) -> None:
    """Translate AtomicUnitError selection IDs to gutter IDs and exit with user-friendly message.

    Args:
        error: The AtomicUnitError containing selection IDs
        rendered: The RenderedBatchDisplay with gutter<->selection ID mapping
        operation_verb: Operation name for error message (e.g., "apply", "include")
        batch_name: Name of the batch

    Raises:
        CommandError: Always exits with translated error message
    """
    from ..data.hunk_tracking import format_id_range

    if error.required_selection_ids:
        # Translate required selection IDs to gutter IDs
        gutter_ids = []
        for sel_id in sorted(error.required_selection_ids):
            if sel_id in rendered.selection_id_to_gutter:
                gutter_ids.append(rendered.selection_id_to_gutter[sel_id])

        if gutter_ids:
            required_range = format_id_range(gutter_ids)

            # User-friendly message based on unit kind
            if error.unit_kind == "replacement":
                explanation = _("These lines form a replacement (deletion + addition) and must be selected together.")
            elif error.unit_kind == "deletion_only":
                explanation = _("These lines form a deletion and must be selected together.")
            else:
                explanation = _("These lines must be selected together.")

            exit_with_error(
                _("{explanation}\nUse: --line {range}").format(
                    explanation=explanation,
                    range=required_range
                ))

    # Fallback: show original error
    exit_with_error(_("Failed to {operation} batch '{name}': {error}").format(
        operation=operation_verb,
        name=batch_name,
        error=str(error)
    ))


def translate_batch_file_gutter_ids_to_selection_ids(
    batch_name: str,
    file_path: str,
    selected_ids: set[int] | None,
    action: 'FileReviewAction | str',
) -> tuple[set[int] | None, 'RenderedBatchDisplay | None']:
    """Translate displayed batch-file gutter IDs to internal selection IDs.

    If the IDs came after a fresh matching file review, validate them against
    the complete actions shown by that review before consulting the full batch
    display. Without a matching review, keep the historical raw batch display
    behavior.
    """
    if selected_ids is None:
        return None, None

    from ..data.file_review_state import (
        fresh_batch_review_selection_groups_for_action,
        validate_review_scoped_line_selection,
    )
    from ..data.hunk_tracking import render_batch_file_display

    review_groups = fresh_batch_review_selection_groups_for_action(batch_name, file_path, action)
    if review_groups is not None:
        validate_review_scoped_line_selection(selected_ids, review_groups)

    rendered = render_batch_file_display(batch_name, file_path)
    if rendered is None:
        return selected_ids, None

    display_id_map = (
        rendered.review_gutter_to_selection_id or rendered.gutter_to_selection_id
        if review_groups is not None else
        rendered.gutter_to_selection_id
    )
    rendered_for_messages = (
        replace(
            rendered,
            gutter_to_selection_id=dict(display_id_map),
            selection_id_to_gutter={
                selection_id: gutter_id
                for gutter_id, selection_id in display_id_map.items()
            },
        )
        if review_groups is not None else
        rendered
    )
    selection_ids: set[int] = set()
    for gutter_id in selected_ids:
        if gutter_id in display_id_map:
            selection_ids.add(display_id_map[gutter_id])
        else:
            exit_with_error(
                _("Line ID {id} is not available for this action. Select one of the numbered lines shown for this batch file.").format(
                    id=gutter_id
                )
            )

    return selection_ids, rendered_for_messages


def select_batch_ownership_for_display_ids(
    file_meta: dict,
    batch_source_content: bytes,
    selected_ids: Optional[set[int]],
) -> BatchOwnership:
    """Select ownership from batch file using semantic unit filtering.

    If selected_ids is None, returns full ownership.
    If selected_ids is provided, performs semantic ownership unit selection:
    - Builds ownership units from display reconstruction
    - Selects units matching display IDs
    - Validates atomic unit boundaries are respected
    - Rebuilds ownership from selected units

    Args:
        file_meta: File metadata from batch containing ownership
        batch_source_content: Batch source content (bytes)
        selected_ids: Optional set of display line IDs to select

    Returns:
        BatchOwnership - either full or filtered based on selection

    Raises:
        MergeError: If atomic ownership unit is partially selected
    """
    # Load full ownership from metadata
    ownership = BatchOwnership.from_metadata_dict(file_meta)

    # If no selection, return full ownership
    if selected_ids is None:
        return ownership

    # Build semantic ownership units from display reconstruction
    units = build_ownership_units_from_display(ownership, batch_source_content)

    # Select units matching the display IDs
    selected_units = select_ownership_units_by_display_ids(units, selected_ids)

    # Validate selected units have valid structure
    validate_ownership_units(selected_units)

    # Rebuild ownership from selected units
    return rebuild_ownership_from_units(selected_units)
