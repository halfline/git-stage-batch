"""Shared batch selection and filtering logic for commands."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Optional

from .ownership import (
    BatchOwnership,
    select_ownership_units_by_display_ids,
    validate_ownership_units,
    rebuild_ownership_from_units,
)
from .ownership_units import build_ownership_units_from_batch_source_lines
from ..core.line_selection import (
    LineRanges,
    LineSelection,
    parse_line_selection,
    parse_line_selection_ranges,
)
from ..data.batch_selected_changes import (
    require_current_selected_batch_binary_file_for_batch,
    require_current_selected_batch_gitlink_file_for_batch,
)
from ..data.progress import format_id_range
from ..data.selected_change.store import (
    SelectedChangeKind,
    read_selected_change_kind,
)
from ..data.selected_change.paths import get_selected_change_file_path
from ..exceptions import CommandError, exit_with_error
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns

if TYPE_CHECKING:
    from ..exceptions import AtomicUnitError
    from ..core.models import LineLevelChange


def _default_live_file_review_command(file_path: str) -> str:
    return f"git-stage-batch show --file {file_path}"


def line_selection_not_valid_message(
    *,
    line_id_specification: str,
    file_path: str,
    review_command: str | None = None,
) -> str:
    """Return the shared invalid line-selection message."""
    command = review_command or _default_live_file_review_command(file_path)
    return _(
        "Line selection {lines} is not valid for {file}.\n"
        "Run '{command}' and choose line IDs from the current file view."
    ).format(lines=line_id_specification, file=file_path, command=command)


def line_changes_display_ids(line_changes: 'LineLevelChange') -> set[int]:
    """Return display IDs that are present in a loaded line view."""
    return {
        line.id
        for line in line_changes.lines
        if line.id is not None
    }


def missing_requested_display_ids(
    line_changes: 'LineLevelChange',
    requested_ids: set[int],
) -> set[int]:
    """Return requested display IDs that do not exist in the line view."""
    return requested_ids - line_changes_display_ids(line_changes)


def require_display_ids_available(
    requested_ids: LineSelection | Iterable[int],
    available_ids: LineSelection | Iterable[int],
    *,
    line_id_specification: str,
    file_path: str,
    review_command: str | None = None,
) -> None:
    """Reject a line selection if any requested display ID is unavailable."""
    requested_ranges = (
        requested_ids
        if isinstance(requested_ids, LineRanges)
        else LineRanges.from_lines(requested_ids)
    )
    available_ranges = (
        available_ids
        if isinstance(available_ids, LineRanges)
        else LineRanges.from_lines(available_ids)
    )
    if requested_ranges.difference(available_ranges):
        exit_with_error(
            line_selection_not_valid_message(
                line_id_specification=line_id_specification,
                file_path=file_path,
                review_command=review_command,
            )
        )


def require_line_selection_in_view(
    line_changes: 'LineLevelChange',
    requested_ids: set[int],
    *,
    line_id_specification: str,
    review_command: str | None = None,
) -> None:
    """Reject a line selection if any requested ID is absent from a line view."""
    require_display_ids_available(
        requested_ids,
        line_changes_display_ids(line_changes),
        line_id_specification=line_id_specification,
        file_path=line_changes.path,
        review_command=review_command,
    )


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
        # If file is empty string, use selected hunk's file
        if file == "":
            file_to_use = get_selected_change_file_path()
            if file_to_use is None:
                exit_with_error(_("No selected hunk. Run 'show' first or specify file path."))
        else:
            file_to_use = file

        target_file = _get_batch_file_for_line_operation(batch_name, all_files, file_to_use)
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


def resolve_current_batch_atomic_file_scope(
    batch_name: str,
    all_files: dict[str, dict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    line_ids: Optional[str] = None,
) -> Optional[str]:
    """Resolve a pathless whole-file batch action through an atomic selection.

    Selected batch binaries and submodule pointers are atomic current-file
    selections. Both the bare command and `--file` with no path are pathless
    whole-file actions, so both must revalidate cached batch state before
    narrowing to the selected file.
    """
    if patterns is not None or line_ids is not None or file not in (None, ""):
        return file

    selected_kind = read_selected_change_kind()
    if selected_kind == SelectedChangeKind.BATCH_BINARY:
        selected_file = require_current_selected_batch_binary_file_for_batch(batch_name, all_files)
        return selected_file if selected_file is not None else file
    if selected_kind == SelectedChangeKind.BATCH_GITLINK:
        selected_file = require_current_selected_batch_gitlink_file_for_batch(batch_name, all_files)
        return selected_file if selected_file is not None else file

    return file


def resolve_current_batch_binary_file_scope(
    batch_name: str,
    all_files: dict[str, dict],
    file: Optional[str] = None,
    patterns: Optional[list[str]] = None,
    line_ids: Optional[str] = None,
) -> Optional[str]:
    """Backward-compatible wrapper for atomic batch selections."""
    return resolve_current_batch_atomic_file_scope(
        batch_name,
        all_files,
        file,
        patterns,
        line_ids,
    )


def _get_batch_file_for_line_operation(
    batch_name: str,
    all_files: dict[str, dict],
    file: str | None,
) -> str:
    """Determine which file in batch to operate on."""
    files = sorted(all_files.keys())

    if not files:
        raise CommandError(f"Batch '{batch_name}' is empty")

    if file is None:
        return files[0]

    if file not in all_files:
        raise CommandError(f"File '{file}' not found in batch '{batch_name}'")

    return file


def require_single_file_context_for_line_selection(
    batch_name: str,
    files: dict[str, dict],
    line_ids: Optional[str],
    operation_verb: str,
) -> Optional[set[int]]:
    """Parse line IDs as a set and enforce single-file context requirement.

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
    if not _line_selection_has_single_file_context(
        batch_name,
        files,
        line_ids,
        operation_verb,
    ):
        return None

    return set(parse_line_selection(line_ids))


def require_single_file_context_for_line_selection_ranges(
    batch_name: str,
    files: dict[str, dict],
    line_ids: Optional[str],
    operation_verb: str,
) -> Optional[LineRanges]:
    """Parse line IDs as ranges and enforce single-file context requirement."""
    if not _line_selection_has_single_file_context(
        batch_name,
        files,
        line_ids,
        operation_verb,
    ):
        return None

    return parse_line_selection_ranges(line_ids)


def _line_selection_has_single_file_context(
    batch_name: str,
    files: dict[str, dict],
    line_ids: Optional[str],
    operation_verb: str,
) -> bool:
    """Return True when a line selection can be interpreted for one file."""
    if line_ids is None:
        return False

    if len(files) != 1:
        exit_with_error(
            _("Line-level {operation} (--line) requires single-file context.\n"
              "Use --file to specify a file, or open one listed file with "
              "'show --from {name} --file PATH'.").format(
                operation=operation_verb,
                name=batch_name
            )
        )

    return True


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


@contextmanager
def acquire_batch_ownership_for_display_ids_from_lines(
    file_meta: dict,
    batch_source_lines: Sequence[bytes],
    selected_ids: Optional[set[int]],
) -> Iterator[BatchOwnership]:
    """Acquire selected ownership for indexed batch-source lines."""
    with BatchOwnership.acquire_for_metadata_dict(file_meta) as ownership:
        if selected_ids is None:
            yield ownership
            return

        yield _select_batch_ownership_from_lines(
            ownership,
            batch_source_lines,
            selected_ids,
        )


def _select_batch_ownership_from_lines(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
    selected_ids: set[int],
) -> BatchOwnership:
    """Select ownership from reconstructed display units."""
    units = build_ownership_units_from_batch_source_lines(
        ownership,
        batch_source_lines,
    )
    selected_units = select_ownership_units_by_display_ids(units, selected_ids)
    validate_ownership_units(selected_units)
    return rebuild_ownership_from_units(selected_units)
