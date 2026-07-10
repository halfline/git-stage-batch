"""Shared batch selection and filtering logic for commands."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Optional

from .ownership import BatchOwnership
from .ownership_units import (
    build_ownership_units_from_batch_source_lines,
)
from .ownership_unit_rebuild import rebuild_ownership_from_units
from .ownership_unit_selection import select_ownership_units_by_display_ids
from .ownership_unit_validation import validate_ownership_units
from ..core.line_selection import (
    LineRanges,
    LineSelection,
    parse_line_selection,
    parse_line_selection_ranges,
)
from ..exceptions import CommandError
from ..i18n import _

if TYPE_CHECKING:
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
        raise CommandError(
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
        CommandError: If line_ids provided but multiple files in scope
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
        raise CommandError(
            _("Line-level {operation} (--line) requires single-file context.\n"
              "Use --file to specify a file, or open one listed file with "
              "'show --from {name} --file PATH'.").format(
                operation=operation_verb,
                name=batch_name
            )
        )

    return True


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
