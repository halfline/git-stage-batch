"""Translate selected diff lines into batch ownership claims."""

from __future__ import annotations

from .absence_content import (
    build_absence_content_from_range as _build_absence_content_from_range,
)
from .model import BatchOwnership
from .absence_claims import AbsenceClaim
from .claims import LineRangeBuilder, presence_claims_from_source_lines
from .line_entries import (
    LineEntryContentSequence as _LineEntryContentSequence,
    ReplacementUnitBuilder as _ReplacementUnitBuilder,
)
from .references import BaselineReference
from .replacement_units import (
    ReplacementUnit as _ReplacementUnit,
    normalize_replacement_units,
)


def detect_stale_batch_source_for_selection(selected_lines: list) -> bool:
    """Detect if selected lines cannot be expressed in current batch source.

    Returns True if any context/addition line lacks a source coordinate,
    indicating the batch source is stale and must be advanced before
    translation.

    Args:
        selected_lines: List of LineEntry objects to check

    Returns:
        True if batch source is stale, False otherwise
    """
    for line in selected_lines:
        # Context and addition lines need source coordinates for claim
        # translation and deletion boundaries.
        if line.kind in (' ', '+') and line.source_line is None:
            return True
        # A None deletion anchor is only current for deletions before line 1.
        if (
            line.kind == '-'
            and line.source_line is None
            and line.old_line_number is not None
            and line.old_line_number > 1
        ):
            return True
    return False


def translate_lines_to_batch_ownership(selected_lines: list) -> BatchOwnership:
    """Translate display lines to batch source ownership.

    Creates presence claims and suppression constraints (deletion_claims).
    Each contiguous run of deletions becomes a separate AbsenceClaim.

    This function assumes all selected lines can be expressed in batch source
    space. Call detect_stale_batch_source_for_selection() first and handle stale
    sources before calling this function. If a required source coordinate is
    missing, this raises an error instead of dropping the affected change.

    Args:
        selected_lines: List of LineEntry objects to translate

    Returns:
        BatchOwnership with presence claims and absence claims

    Raises:
        ValueError: If a required source coordinate is missing
    """
    # Translate to batch source-space ownership
    # Diff shows index→working tree, batch source = working tree
    # Addition lines exist in batch source → presence claims
    # Context lines only provide structural boundaries for those changes
    # Deletion lines don't exist in batch source → absence claims (suppression)

    content_view = _LineEntryContentSequence(selected_lines)
    claimed_source_lines = LineRangeBuilder()
    presence_baseline_references: dict[int, BaselineReference] = {}
    absence_claims: list[AbsenceClaim] = []
    replacement_units: list[_ReplacementUnit] = []

    # Track current deletion run
    current_absence_anchor: int | None = None
    current_absence_start: int | None = None
    current_absence_stop: int | None = None
    current_absence_old_start: int | None = None
    current_absence_old_end: int | None = None
    current_absence_after_content: bytes | None = None
    previous_old_line: int | None = None
    previous_old_content: bytes | None = None
    active_replacement_unit: _ReplacementUnitBuilder | None = None

    def finish_replacement_unit(
        builder: _ReplacementUnitBuilder | None,
    ) -> None:
        if builder is not None:
            replacement_units.append(builder.finish())

    def flush_absence_run() -> list[int]:
        """Finalize current deletion run as an AbsenceClaim."""
        nonlocal current_absence_anchor
        nonlocal current_absence_start
        nonlocal current_absence_stop
        nonlocal current_absence_old_start
        nonlocal current_absence_old_end
        nonlocal current_absence_after_content
        if current_absence_start is None or current_absence_stop is None:
            return []

        content_lines = _build_absence_content_from_range(
            content_view,
            current_absence_start,
            current_absence_stop,
        )
        baseline_reference = None
        if (
            current_absence_old_start is not None
            and current_absence_old_end is not None
        ):
            before_line = current_absence_old_end + 1
            before_content = None
            for following_index in range(
                current_absence_stop,
                len(selected_lines),
            ):
                following_line = selected_lines[following_index]
                if (
                    following_line.kind in {" ", "-"}
                    and following_line.old_line_number == before_line
                ):
                    before_content = following_line.text_bytes
                    break
            baseline_reference = BaselineReference(
                after_line=(
                    current_absence_old_start - 1
                    if current_absence_old_start > 1
                    else None
                ),
                after_content=current_absence_after_content,
                has_after_line=True,
                before_line=before_line if before_content is not None else None,
                before_content=before_content,
                has_before_line=before_content is not None,
            )
        absence_claims.append(
            AbsenceClaim(
                anchor_line=current_absence_anchor,
                content_lines=content_lines,
                baseline_reference=baseline_reference,
            )
        )
        absence_index = len(absence_claims) - 1
        current_absence_start = None
        current_absence_stop = None
        current_absence_anchor = None
        current_absence_old_start = None
        current_absence_old_end = None
        current_absence_after_content = None
        return [absence_index]

    for index, line in enumerate(selected_lines):
        if line.kind in (' ', '+'):
            # Context or addition: exists in batch source (working tree)
            # Flush any pending deletion run
            flushed_deletion_indices = flush_absence_run()

            if line.source_line is None:
                raise ValueError(
                    f"Cannot translate line to batch ownership: source_line is None "
                    f"(kind={line.kind!r}, text={line.display_text()!r}). "
                    f"Batch source is stale and must be advanced before translation."
                )

            if line.kind == '+':
                claimed_source_lines.add_line(line.source_line)
                if line.has_baseline_reference_after:
                    presence_baseline_references[line.source_line] = (
                        BaselineReference(
                            after_line=line.baseline_reference_after_line,
                            after_content=(
                                line.baseline_reference_after_text_bytes
                            ),
                            has_after_line=line.has_baseline_reference_after,
                            before_line=line.baseline_reference_before_line,
                            before_content=(
                                line.baseline_reference_before_text_bytes
                            ),
                            has_before_line=line.has_baseline_reference_before,
                        )
                    )
                if flushed_deletion_indices:
                    finish_replacement_unit(active_replacement_unit)
                    active_replacement_unit = _ReplacementUnitBuilder(
                        deletion_indices=flushed_deletion_indices,
                    )

                if active_replacement_unit is not None:
                    active_replacement_unit.add_presence_line(line.source_line)
            else:
                finish_replacement_unit(active_replacement_unit)
                active_replacement_unit = None

            # Update anchor for next deletion run
            current_absence_anchor = line.source_line
            if line.kind == " " and line.old_line_number is not None:
                previous_old_line = line.old_line_number
                previous_old_content = line.text_bytes

        elif line.kind == '-':
            finish_replacement_unit(active_replacement_unit)
            active_replacement_unit = None
            if (
                current_absence_start is not None
                and current_absence_old_end is not None
                and line.old_line_number is not None
                and line.old_line_number != current_absence_old_end + 1
            ):
                flush_absence_run()
            # Deletion: suppression constraint
            # Anchor each deletion run at its first deleted line. A None anchor
            # means the run starts before the first source line and must not be
            # overwritten by later deleted lines in the same run.
            if current_absence_start is None:
                current_absence_start = index
                current_absence_anchor = line.source_line
                current_absence_old_start = line.old_line_number
                if (
                    line.old_line_number is not None
                    and previous_old_line == line.old_line_number - 1
                ):
                    current_absence_after_content = previous_old_content
            if line.old_line_number is not None:
                current_absence_old_end = line.old_line_number
                previous_old_line = line.old_line_number
                previous_old_content = line.text_bytes
            current_absence_stop = index + 1

    # Flush any final deletion run
    flush_absence_run()
    finish_replacement_unit(active_replacement_unit)

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            claimed_source_lines.finish(),
            presence_baseline_references,
        ),
        deletions=absence_claims,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(absence_claims),
        ),
    )
