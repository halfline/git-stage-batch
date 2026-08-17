"""Shared batch selection and filtering logic for commands."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import shlex
from typing import TYPE_CHECKING, Optional

from .ownership.model import BatchOwnership
from .ownership.metadata_loading import acquire_ownership_for_metadata_dict
from .ownership.units import (
    build_ownership_units_from_batch_source_lines,
)
from .ownership.unit_rebuild import rebuild_ownership_from_units
from .ownership.unit_selection import select_ownership_units_by_display_ids
from .ownership.unit_types import OwnershipUnit, OwnershipUnitKind
from .ownership.unit_validation import validate_ownership_units
from .state.metadata_types import BatchFileMetadataDict
from ..core.line_selection import (
    LineRangeBuilder,
    LineRanges,
    LineSelection,
    parse_line_selection,
    parse_line_selection_ranges,
)
from ..exceptions import CommandError
from ..git_paths import display_path, terminal_safe_shell_quote
from ..i18n import _

if TYPE_CHECKING:
    from ..core.models import LineLevelChange


def _double_quote_shell_argument(value: str) -> str:
    """Shell-quote an argument, preferring visible double quotes."""
    if not all(character.isprintable() for character in value):
        return terminal_safe_shell_quote(value)
    if "!" in value:
        return shlex.quote(value)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _default_live_file_review_command(file_path: str) -> str:
    quoted_file_path = _double_quote_shell_argument(file_path)
    return f"git-stage-batch show --file {quoted_file_path}"


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
    ).format(
        lines=line_id_specification,
        file=display_path(file_path),
        command=command,
    )


def line_changes_display_ids(line_changes: "LineLevelChange") -> set[int]:
    """Return display IDs that are present in a loaded line view."""
    return {line.id for line in line_changes.lines if line.id is not None}


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
    line_changes: "LineLevelChange",
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
    files: dict[str, BatchFileMetadataDict],
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

    assert line_ids is not None
    return set(parse_command_line_selection(line_ids))


def require_single_file_context_for_line_selection_ranges(
    batch_name: str,
    files: dict[str, BatchFileMetadataDict],
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

    assert line_ids is not None
    try:
        return parse_line_selection_ranges(line_ids)
    except ValueError as error:
        raise CommandError(str(error)) from error


def parse_command_line_selection(line_ids: str) -> list[int]:
    """Parse command-line line IDs without exposing parser tracebacks."""
    try:
        return parse_line_selection(line_ids)
    except ValueError as error:
        raise CommandError(str(error)) from error


def _line_selection_has_single_file_context(
    batch_name: str,
    files: dict[str, BatchFileMetadataDict],
    line_ids: Optional[str],
    operation_verb: str,
) -> bool:
    """Return True when a line selection can be interpreted for one file."""
    if line_ids is None:
        return False

    if len(files) != 1:
        raise CommandError(
            _(
                "Line-level {operation} (--line) requires single-file context.\n"
                "Use --file to specify a file, or open one listed file with "
                "'show --from {name} --file PATH'."
            ).format(operation=operation_verb, name=batch_name)
        )

    return True


@contextmanager
def acquire_batch_ownership_for_display_ids_from_lines(
    file_meta: BatchFileMetadataDict,
    batch_source_lines: Sequence[bytes],
    selected_ids: Optional[set[int]],
    *,
    spool_dir: str | Path | None = None,
) -> Iterator[BatchOwnership]:
    """Acquire selected ownership for indexed batch-source lines."""
    with acquire_ownership_for_metadata_dict(
        file_meta,
        spool_dir=spool_dir,
    ) as ownership:
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
    completed_units = _complete_selected_legacy_replacements(
        ownership,
        units,
        selected_units,
    )
    validate_ownership_units(completed_units)
    return rebuild_ownership_from_units(completed_units)


def _containing_range_end(
    ranges: Sequence[tuple[int, int]],
    line: int,
) -> int | None:
    """Return the end of the normalized range containing ``line``."""
    low = 0
    high = len(ranges)
    while low < high:
        middle = (low + high) // 2
        if ranges[middle][0] <= line:
            low = middle + 1
        else:
            high = middle
    range_index = low - 1
    if range_index < 0:
        return None
    _range_start, range_end = ranges[range_index]
    return range_end if line <= range_end else None


def _legacy_replacement_completion_end(
    unit: OwnershipUnit,
    presence_ranges: Sequence[tuple[int, int]],
) -> int | None:
    """Return the end of one unpersisted legacy replacement run."""
    if (
        unit.kind is not OwnershipUnitKind.REPLACEMENT
        or unit.preserves_replacement_unit
        or len(unit.deletion_claims) != 1
    ):
        return None
    deletion = unit.deletion_claims[0]
    if not deletion.content_lines:
        return None
    anchor_line = deletion.anchor_line
    if anchor_line is None:
        replacement_start = 1
    elif type(anchor_line) is int and anchor_line >= 0:
        replacement_start = anchor_line + 1
    else:
        return None
    if replacement_start not in unit.claimed_source_lines:
        return None
    return _containing_range_end(presence_ranges, replacement_start)


def _complete_selected_legacy_replacements(
    ownership: BatchOwnership,
    units: Sequence[OwnershipUnit],
    selected_units: Sequence[OwnershipUnit],
) -> list[OwnershipUnit]:
    """Keep a selected legacy replacement's contiguous continuation coupled.

    Old metadata inferred a replacement from a deletion immediately followed by
    a presence run.  Display units intentionally expose the later presence
    lines separately for fine-grained reset, but merge actions still need the
    complete run.  Reconstruct that coupling only in the transient selected
    ownership; persisted metadata remains unchanged.
    """
    if not selected_units:
        return []

    presence_ranges = ownership.presence_line_set().ranges()
    completed: list[OwnershipUnit] = []
    selected_index = 0
    unit_index = 0
    while unit_index < len(units) and selected_index < len(selected_units):
        unit = units[unit_index]
        if unit is not selected_units[selected_index]:
            unit_index += 1
            continue

        selected_index += 1
        completion_end = _legacy_replacement_completion_end(
            unit,
            presence_ranges,
        )
        unit_ranges = unit.claimed_source_lines.ranges()
        current_end = unit_ranges[-1][1] if unit_ranges else None
        if (
            completion_end is None
            or current_end is None
            or completion_end <= current_end
        ):
            completed.append(unit)
            unit_index += 1
            continue

        continuation_index = unit_index + 1
        next_source_line = current_end + 1
        claimed_ranges = LineRangeBuilder()
        for range_start, range_end in unit.claimed_source_lines.ranges():
            claimed_ranges.add_range(range_start, range_end)
        display_ranges = LineRangeBuilder()
        for range_start, range_end in unit.display_line_ids.ranges():
            display_ranges.add_range(range_start, range_end)
        baseline_references = dict(unit.baseline_references)
        while continuation_index < len(units) and next_source_line <= completion_end:
            continuation = units[continuation_index]
            continuation_ranges = continuation.claimed_source_lines.ranges()
            if (
                continuation.kind is not OwnershipUnitKind.PRESENCE_ONLY
                or len(continuation_ranges) != 1
                or continuation_ranges[0][0] != next_source_line
                or continuation_ranges[0][1] > completion_end
            ):
                break
            for range_start, range_end in continuation_ranges:
                claimed_ranges.add_range(range_start, range_end)
            for range_start, range_end in continuation.display_line_ids.ranges():
                display_ranges.add_range(range_start, range_end)
            baseline_references.update(continuation.baseline_references)
            next_source_line = continuation_ranges[0][1] + 1
            if (
                selected_index < len(selected_units)
                and continuation is selected_units[selected_index]
            ):
                selected_index += 1
            continuation_index += 1

        if next_source_line <= completion_end:
            completed.append(unit)
            unit_index += 1
            continue

        completed.append(
            replace(
                unit,
                claimed_source_lines=claimed_ranges.finish(),
                display_line_ids=display_ranges.finish(),
                baseline_references=baseline_references,
                preserves_replacement_unit=True,
            )
        )
        unit_index = continuation_index

    return completed
