"""Structural batch merge using Long Common Subsequence-based alignment."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from .match import LineMapping, match_lines
from ..exceptions import MergeError, MissingAnchorError, AmbiguousAnchorError
from ..i18n import _
from ..utils.text import normalize_line_endings

if TYPE_CHECKING:
    from .ownership import BatchOwnership, DeletionClaim


class RegionKind(Enum):
    """Region kind for baseline restoration correspondence.

    Defines how a source-space region should be restored during discard:
    - EQUAL: Unchanged lines, restored line-by-line
    - INSERT: Source-only (batch added), removed during discard
    - REPLACE_LINE_BY_LINE: Changed region with same size, restored line-by-line
    - REPLACE_BY_HUNK: Changed region with different sizes, restored as whole unit
    """
    EQUAL = auto()
    INSERT = auto()
    REPLACE_LINE_BY_LINE = auto()
    REPLACE_BY_HUNK = auto()


@dataclass
class RealizedEntry:
    """A line in realized content with structural provenance.

    Tracks where each line came from in batch-source space, enabling
    exact anchored boundary resolution for absence constraints.
    """
    content: bytes  # Line content with newline
    source_line: int | None  # Batch-source line number (1-indexed), or None for working-tree extras
    target_line: int | None = None  # Working-tree line number (1-indexed), when known
    is_claimed: bool = False  # True if from a claimed source line (presence constraint)


@dataclass
class BaselineRegion:
    """A source-space region with baseline restoration content.

    Represents one contiguous source-side region and the baseline content
    that should be restored when that region is batch-owned and discarded.

    Region kinds:
    - EQUAL: unchanged lines, restored line-by-line
    - INSERT: source-only (batch added), removed when discarded
    - REPLACE_LINE_BY_LINE: changed region (same size), restored line-by-line
    - REPLACE_BY_HUNK: changed region (different sizes), restored as whole unit
    """
    source_start_line: int          # 1-based inclusive
    source_end_line: int            # 1-based inclusive
    baseline_lines: list[bytes]     # baseline content for restoration
    kind: RegionKind                # Region restoration kind
    is_ambiguous: bool = False
    region_id: int = 0              # Unique region identifier (assigned during construction)


@dataclass
class BaselineCorrespondence:
    """Restoration correspondence from source lines back to baseline regions."""
    line_to_region: dict[int, 'BaselineRegion']
    regions: list['BaselineRegion']

    def get_region_for_source_line(
        self,
        source_line: int
    ) -> 'BaselineRegion | None':
        return self.line_to_region.get(source_line)


@dataclass
class ClaimedRunIntervalFacts:
    """Structural facts about one contiguous run of missing claimed lines.

    These facts make the merge-time safety decision explicit instead of hiding the
    reasoning inside a single trailing-gap threshold.
    """
    run_start: int
    run_end: int
    run_length: int
    before_source_line: int | None
    after_source_line: int | None
    before_target_line: int | None
    after_target_line: int | None
    leading_unmapped_source_gap: int
    trailing_unmapped_source_gap: int
    bracketed_on_both_sides: bool
    bracketed_on_one_side_only: bool
    source_interval_span: int | None
    target_interval_span: int | None
    surrounding_source_gap_outside_run: int | None
    target_lines_after_before_anchor: int | None
    has_deletion_at_before_anchor: bool
    deletion_line_count_at_before_anchor: int


def _check_structural_validity(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    deletions: list,  # list[DeletionClaim]
    source_lines: list[bytes],
    target_lines: list[bytes]
) -> None:
    """Validate that batch can be safely applied given structural alignment.

    Checks:
    1. File hasn't been completely rewritten (zero alignment)
    2. Missing claimed lines have nearby aligned context
    3. Missing deletion anchors have nearby aligned context
    4. Claimed runs have structurally coherent surrounding context

    Check #4 prevents corruption when applying partial selections.
    If claimed lines come from a source region whose surrounding source structure
    no longer maps coherently into the working tree, inserting those lines may
    preserve incompatible working-tree content that should have been replaced.

    Args:
        line_mapping: Alignment between batch source and working tree
        claimed_lines: Set of claimed batch source line numbers
        deletions: List of DeletionClaim objects
        source_lines: Batch source file lines (bytes)
        target_lines: Working tree file lines (bytes)

    Raises:
        MergeError: If structural requirements aren't met
    """
    present_count = sum(1 for line in range(1, len(source_lines) + 1)
                       if line_mapping.is_source_line_present(line))

    if len(target_lines) == 0:
        return

    if present_count == 0 and len(target_lines) > 0:
        if claimed_lines:
            first_claimed = min(claimed_lines)
            raise MergeError(
                _("Cannot reliably place claimed line {line}: file completely rewritten").format(
                    line=first_claimed
                )
            )

    for claimed_line in claimed_lines:
        if claimed_line < 1 or claimed_line > len(source_lines):
            raise MergeError(
                _("Claimed line {line} is out of range (batch source has {count} lines)").format(
                    line=claimed_line,
                    count=len(source_lines)
                )
            )

        if not line_mapping.is_source_line_present(claimed_line):
            has_context_before = False
            has_context_after = False

            for check_line in range(claimed_line - 1, 0, -1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_before = True
                    break

            for check_line in range(claimed_line + 1, len(source_lines) + 1):
                if line_mapping.is_source_line_present(check_line):
                    has_context_after = True
                    break

            if not has_context_before and not has_context_after:
                raise MergeError(
                    _("Cannot reliably place claimed line {line}: surrounding context lost").format(
                        line=claimed_line
                    )
                )

    for deletion in deletions:
        after_line = deletion.anchor_line

        if after_line is not None:
            if after_line < 1 or after_line > len(source_lines):
                raise MergeError(
                    _("Deletion after line {line} is out of range").format(line=after_line)
                )

            if not line_mapping.is_source_line_present(after_line):
                has_context = False
                for check_line in range(max(1, after_line - 3), min(len(source_lines) + 1, after_line + 4)):
                    if check_line != after_line and line_mapping.is_source_line_present(check_line):
                        has_context = True
                        break

                if not has_context and after_line != len(source_lines):
                    raise MergeError(
                        _("Cannot determine deletion position after line {line}: anchor and neighbors missing").format(
                            line=after_line
                        )
                    )

    _check_claimed_region_compatibility(
        line_mapping,
        claimed_lines,
        deletions,
        source_lines,
        target_lines
    )


def _check_claimed_region_compatibility(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    deletions: list,  # list[DeletionClaim]
    source_lines: list[bytes],
    target_lines: list[bytes]
) -> None:
    """Check if claimed lines come from source regions with structurally coherent context.

    Prevents corruption from partial selections where claimed lines are inserted
    from a source region whose surrounding context is structurally incompatible
    with the working tree.

    For each contiguous run of missing claimed lines:
    1. Find the nearest mapped source boundary before the run
    2. Find the nearest mapped source boundary after the run
    3. Map those boundaries into target-space
    4. Compare the source interval around the run with the available target interval
    5. Reject if the run is weakly anchored next to source-only structure that
       does not fit the target interval coherently

    This is conservative. It is not trying to prove the placement is globally
    optimal; it is trying to reject cases where the run clearly comes from a
    different structural neighborhood than the working tree currently has.

    Args:
        line_mapping: Alignment between source and working tree
        claimed_lines: Set of claimed source line numbers
        source_lines: Batch source lines
        target_lines: Working tree lines

    Raises:
        MergeError: If claimed lines come from incompatible source region
    """
    sorted_missing = _get_missing_claimed_lines(
        line_mapping,
        claimed_lines,
        source_lines
    )

    if not sorted_missing or len(target_lines) == 0:
        return

    for run_start, run_end in _build_contiguous_runs(sorted_missing):
        facts = _collect_claimed_run_interval_facts(
            run_start,
            run_end,
            line_mapping,
            source_lines,
            target_lines,
            deletions
        )

        if not _is_claimed_run_structurally_coherent(facts):
            raise MergeError(
                _("Batch was created from a different version of the file")
            )


def _get_missing_claimed_lines(
    line_mapping: LineMapping,
    claimed_lines: set[int],
    source_lines: list[bytes]
) -> list[int]:
    """Return claimed source lines that are not present in the working tree."""
    missing_claimed = []

    for line_num in sorted(claimed_lines):
        if 1 <= line_num <= len(source_lines):
            if line_mapping.get_target_line_from_source_line(line_num) is None:
                missing_claimed.append(line_num)

    return missing_claimed


def _build_contiguous_runs(sorted_line_numbers: list[int]) -> list[tuple[int, int]]:
    """Build contiguous inclusive runs from sorted line numbers."""
    if not sorted_line_numbers:
        return []

    runs = []
    run_start = sorted_line_numbers[0]
    run_end = sorted_line_numbers[0]

    for line_num in sorted_line_numbers[1:]:
        if line_num == run_end + 1:
            run_end = line_num
        else:
            runs.append((run_start, run_end))
            run_start = line_num
            run_end = line_num

    runs.append((run_start, run_end))
    return runs


def _find_nearest_mapped_source_line_before(
    line_mapping: LineMapping,
    source_line: int
) -> int | None:
    """Find the nearest mapped source line strictly before the given line."""
    for check_line in range(source_line - 1, 0, -1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _find_nearest_mapped_source_line_after(
    line_mapping: LineMapping,
    source_line: int,
    max_source_line: int
) -> int | None:
    """Find the nearest mapped source line strictly after the given line."""
    for check_line in range(source_line + 1, max_source_line + 1):
        if line_mapping.get_target_line_from_source_line(check_line) is not None:
            return check_line

    return None


def _collect_claimed_run_interval_facts(
    run_start: int,
    run_end: int,
    line_mapping: LineMapping,
    source_lines: list[bytes],
    target_lines: list[bytes],
    deletions: list
) -> ClaimedRunIntervalFacts:
    """Collect explicit structural facts about one missing claimed run."""
    before_source_line = _find_nearest_mapped_source_line_before(
        line_mapping,
        run_start
    )
    after_source_line = _find_nearest_mapped_source_line_after(
        line_mapping,
        run_end,
        len(source_lines)
    )

    before_target_line = None
    after_target_line = None

    if before_source_line is not None:
        before_target_line = line_mapping.get_target_line_from_source_line(before_source_line)

    if after_source_line is not None:
        after_target_line = line_mapping.get_target_line_from_source_line(after_source_line)

    leading_unmapped_source_gap = 0
    if before_source_line is not None:
        leading_unmapped_source_gap = run_start - before_source_line - 1

    trailing_unmapped_source_gap = 0
    if after_source_line is not None:
        trailing_unmapped_source_gap = after_source_line - run_end - 1
    else:
        trailing_unmapped_source_gap = len(source_lines) - run_end

    bracketed_on_both_sides = (
        before_source_line is not None and
        after_source_line is not None and
        before_target_line is not None and
        after_target_line is not None
    )
    bracketed_on_one_side_only = (
        (before_source_line is None) != (after_source_line is None)
    )

    source_interval_span = None
    target_interval_span = None
    surrounding_source_gap_outside_run = None
    target_lines_after_before_anchor = None
    has_deletion_at_before_anchor = False
    deletion_line_count_at_before_anchor = 0

    if bracketed_on_both_sides:
        source_interval_span = after_source_line - before_source_line - 1
        target_interval_span = after_target_line - before_target_line - 1
        surrounding_source_gap_outside_run = source_interval_span - (run_end - run_start + 1)
    elif before_target_line is not None and after_target_line is None:
        target_lines_after_before_anchor = len(target_lines) - before_target_line

    if before_source_line is not None:
        deletion_line_count_at_before_anchor = sum(
            len(deletion.content_lines)
            for deletion in deletions
            if deletion.anchor_line == before_source_line
        )
        has_deletion_at_before_anchor = deletion_line_count_at_before_anchor > 0

    return ClaimedRunIntervalFacts(
        run_start=run_start,
        run_end=run_end,
        run_length=run_end - run_start + 1,
        before_source_line=before_source_line,
        after_source_line=after_source_line,
        before_target_line=before_target_line,
        after_target_line=after_target_line,
        leading_unmapped_source_gap=leading_unmapped_source_gap,
        trailing_unmapped_source_gap=trailing_unmapped_source_gap,
        bracketed_on_both_sides=bracketed_on_both_sides,
        bracketed_on_one_side_only=bracketed_on_one_side_only,
        source_interval_span=source_interval_span,
        target_interval_span=target_interval_span,
        surrounding_source_gap_outside_run=surrounding_source_gap_outside_run,
        target_lines_after_before_anchor=target_lines_after_before_anchor,
        has_deletion_at_before_anchor=has_deletion_at_before_anchor,
        deletion_line_count_at_before_anchor=deletion_line_count_at_before_anchor,
    )


def _is_claimed_run_structurally_coherent(
    facts: ClaimedRunIntervalFacts
) -> bool:
    """Check if a missing claimed run sits in a coherent source/target interval.

    This does not try to prove the merge is globally correct. It makes a
    conservative local decision from explicit interval facts.

    Unsafe patterns:
    - No mapped anchors at all
    - Both-side anchors exist but are inverted in target-space
    - A large trailing source-only gap sits immediately after the run and the
      before/after target interval is too small to plausibly absorb the
      surrounding source structure
    - The run is anchored only on one side and a large source-only gap extends
      away from the run on the unanchored side
    """
    significant_trailing_gap = facts.trailing_unmapped_source_gap >= 3
    significant_leading_gap = facts.leading_unmapped_source_gap >= 3

    if not facts.bracketed_on_both_sides and not facts.bracketed_on_one_side_only:
        return False

    if facts.bracketed_on_both_sides:
        if facts.before_target_line is None or facts.after_target_line is None:
            return False

        if facts.before_target_line >= facts.after_target_line:
            return False

        if significant_trailing_gap:
            if facts.target_interval_span is None or facts.surrounding_source_gap_outside_run is None:
                return False

            # There is substantial source-side structure after the run before the
            # next reliable source anchor, but almost no room for it in target-space.
            # This is the characteristic shape of the corruption case: the selected
            # run came from a neighborhood with extra source-only structure, so
            # inserting it would preserve incompatible target content nearby.
            if facts.target_interval_span < facts.surrounding_source_gap_outside_run:
                return False

            # Even if the overall interval is not smaller, a run followed by a large
            # source-only tail with little or no target interval is still too weakly
            # bracketed to trust.
            if facts.target_interval_span <= facts.run_length:
                return False

        return True

    # Exactly one-sided anchoring. Be stricter because placement depends on only
    # one reliable boundary.
    if facts.before_source_line is not None and facts.after_source_line is None:
        if significant_trailing_gap:
            if facts.target_lines_after_before_anchor is None:
                return False

            # A source-only tail after the selected run is safe when applying
            # into an empty target tail: this is the append/interleave case
            # that lets independent batches compose in either order.
            if facts.target_lines_after_before_anchor == 0:
                return True

            # A replacement can also be safe with target content after the
            # anchor when an absence constraint at that same boundary removes
            # the whole target tail before the new claimed lines are inserted.
            if (
                facts.has_deletion_at_before_anchor and
                facts.target_lines_after_before_anchor <= facts.deletion_line_count_at_before_anchor
            ):
                return True

            return False
        return True

    if facts.before_source_line is None and facts.after_source_line is not None:
        if significant_leading_gap:
            return False
        return True

    return False


def _apply_presence_constraints(
    source_lines: list[bytes],
    working_lines: list[bytes],
    claimed_line_set: set[int]
) -> list[RealizedEntry]:
    """Apply presence constraints: ensure all claimed lines exist in result.

    Uses structural alignment to determine which claimed lines are already present
    and adds missing ones at appropriate positions. Returns structured entries
    that preserve batch-source provenance for anchored absence constraints.

    Args:
        source_lines: Batch source file lines (bytes with newlines)
        working_lines: Working tree file lines (bytes with newlines)
        claimed_line_set: Set of source line numbers that must be present

    Returns:
        Realized entries with all claimed lines present and provenance preserved
    """
    if not claimed_line_set:
        mapping = match_lines(source_lines, working_lines)

        result: list[RealizedEntry] = []
        for working_idx, working_line in enumerate(working_lines):
            source_line = mapping.get_source_line_from_target_line(working_idx + 1)
            result.append(RealizedEntry(
                content=working_line,
                source_line=source_line,
                target_line=working_idx + 1,
                is_claimed=False,
            ))
        return result

    mapping = match_lines(source_lines, working_lines)

    present_claimed: dict[int, int] = {}
    missing_claimed: dict[int, bytes] = {}

    for source_line in claimed_line_set:
        if 1 <= source_line <= len(source_lines):
            working_line = mapping.get_target_line_from_source_line(source_line)
            if working_line is not None:
                present_claimed[source_line] = working_line
            else:
                missing_claimed[source_line] = source_lines[source_line - 1]

    if not missing_claimed:
        result: list[RealizedEntry] = []
        for working_idx, working_line in enumerate(working_lines):
            source_line = mapping.get_source_line_from_target_line(working_idx + 1)
            is_claimed = source_line in claimed_line_set if source_line else False
            result.append(RealizedEntry(
                content=working_line,
                source_line=source_line,
                target_line=working_idx + 1,
                is_claimed=is_claimed,
            ))
        return result

    result: list[RealizedEntry] = []
    working_idx = 0

    for source_line in range(1, len(source_lines) + 1):
        working_line = mapping.get_target_line_from_source_line(source_line)

        if working_line is not None:
            while working_idx < working_line - 1:
                result.append(RealizedEntry(
                    content=working_lines[working_idx],
                    source_line=None,
                    target_line=working_idx + 1,
                    is_claimed=False
                ))
                working_idx += 1

            is_claimed = source_line in claimed_line_set
            if is_claimed:
                result.append(RealizedEntry(
                    content=source_lines[source_line - 1],
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=True
                ))
            else:
                result.append(RealizedEntry(
                    content=working_lines[working_idx],
                    source_line=source_line,
                    target_line=working_idx + 1,
                    is_claimed=False
                ))
            working_idx += 1
        else:
            if source_line in missing_claimed:
                result.append(RealizedEntry(
                    content=missing_claimed[source_line],
                    source_line=source_line,
                    is_claimed=True
                ))

    while working_idx < len(working_lines):
        result.append(RealizedEntry(
            content=working_lines[working_idx],
            source_line=None,
            target_line=working_idx + 1,
            is_claimed=False
        ))
        working_idx += 1

    return result


def _apply_absence_constraints(
    entries: list[RealizedEntry],
    deletion_claims: list['DeletionClaim'],
    *,
    strict: bool = True
) -> list[RealizedEntry]:
    """Apply absence constraints with boundary enforcement.

    For each deletion claim:
    1. Find the structural boundary after the anchor line
    2. Suppress forbidden sequence at that boundary using appropriate mode

    Two enforcement modes controlled by 'strict' parameter:

    Strict mode (strict=True) - for merge_batch():
    - Used when merging into live working tree that may have diverged
    - Exact match at boundary: suppress
    - Found nearby but not at boundary: raise MergeError (structural conflict)
    - Not found: no-op (already suppressed or never existed)

    Realization mode (strict=False) - for _build_realized_content():
    - Used when building display/storage content from baseline
    - Exact match at boundary: suppress
    - Not at boundary: no-op (baseline may not have content there)

    Both modes fail if anchor boundary itself cannot be determined (MissingAnchorError
    or AmbiguousAnchorError), as this indicates a real structural inconsistency.

    Args:
        entries: Realized entries with source provenance from presence pass
        deletion_claims: Absence constraints with structural anchors
        strict: If True, use strict enforcement (merge). If False, lenient (realization)

    Returns:
        Entries with forbidden sequences suppressed at their anchored boundaries

    Raises:
        MissingAnchorError: If anchor line not present in realized content
        AmbiguousAnchorError: If anchor boundary cannot be determined uniquely
        MergeError: If strict=True and sequence found nearby but not at boundary
    """
    if not deletion_claims:
        return entries

    result = entries[:]

    suppress_fn = _suppress_at_boundary_strict if strict else _suppress_at_boundary_for_realization

    for claim in deletion_claims:
        if not claim.content_lines:
            continue

        # Find boundary (fails if ambiguous or missing - appropriate for both modes)
        try:
            boundary = _find_boundary_after_source_line(result, claim.anchor_line)
        except MissingAnchorError:
            if strict:
                raise
            boundary = _find_realization_fallback_boundary(result, claim.anchor_line)

        # Normalize deletion content for comparison
        forbidden_sequence = [
            normalize_line_endings(line)
            for line in claim.content_lines
        ]

        result = suppress_fn(result, boundary, forbidden_sequence)

    return result


def _missing_claimed_lines(
    entries: list[RealizedEntry],
    claimed_line_set: set[int]
) -> set[int]:
    """Return claimed source lines that are not present as claimed entries."""
    present_claimed = {
        entry.source_line
        for entry in entries
        if entry.is_claimed and entry.source_line is not None
    }
    return claimed_line_set - present_claimed


def _satisfy_constraints(
    source_lines: list[bytes],
    working_lines: list[bytes],
    claimed_line_set: set[int],
    deletion_claims: list['DeletionClaim'],
    *,
    strict: bool = True
) -> list[RealizedEntry]:
    """Apply presence and absence constraints until claimed lines survive."""
    realized_entries = _apply_presence_constraints(
        source_lines,
        working_lines,
        claimed_line_set
    )

    realized_entries = _apply_absence_constraints(
        realized_entries,
        deletion_claims,
        strict=strict
    )

    if not _missing_claimed_lines(realized_entries, claimed_line_set):
        return realized_entries

    current_lines = [entry.content for entry in realized_entries]
    realized_entries = _apply_presence_constraints(
        source_lines,
        current_lines,
        claimed_line_set
    )

    realized_entries = _apply_absence_constraints(
        realized_entries,
        deletion_claims,
        strict=strict
    )

    missing_claimed = _missing_claimed_lines(realized_entries, claimed_line_set)
    if missing_claimed:
        if not strict:
            return realized_entries
        first_missing = min(missing_claimed)
        raise MergeError(
            _("Cannot satisfy claimed line {line}: removed by absence constraints").format(
                line=first_missing
            )
        )

    return realized_entries


def _find_realization_fallback_boundary(
    entries: list[RealizedEntry],
    source_line: int | None
) -> int:
    """Find a lenient boundary for realization when an anchor is absent.

    Realized batch content may intentionally omit unclaimed source-only lines,
    and earlier absence constraints may remove entries that carried later anchor
    provenance. In that storage/display path, fall back to the nearest earlier
    realized source line and let exact sequence matching decide whether anything
    should be suppressed.
    """
    if source_line is None:
        return 0

    prior_source_lines = [
        entry.source_line
        for entry in entries
        if entry.source_line is not None and entry.source_line < source_line
    ]
    if not prior_source_lines:
        return 0

    return _find_boundary_after_source_line(entries, max(prior_source_lines))


def _find_boundary_after_source_line(
    entries: list[RealizedEntry],
    source_line: int | None
) -> int:
    """Find the index representing the boundary after a source line.

    The boundary is the position where content anchored "after source line N"
    would appear in the realized output.

    This is strict about ambiguity: if multiple distinct occurrences of the
    same source line exist (e.g., from duplicates or working tree extras),
    we verify there is exactly one claimed occurrence to use as the anchor.

    Args:
        entries: Realized entries with source provenance
        source_line: Anchor line (1-indexed), or None for start-of-file

    Returns:
        Index in entries representing the boundary (0 = start of file)

    Raises:
        MissingAnchorError: If anchor line not present in realized content
        AmbiguousAnchorError: If boundary cannot be determined uniquely
    """
    if source_line is None:
        return 0

    matching_indices = []
    claimed_indices = []

    for i, entry in enumerate(entries):
        if entry.source_line == source_line:
            matching_indices.append(i)
            if entry.is_claimed:
                claimed_indices.append(i)

    if not matching_indices:
        raise MissingAnchorError(
            _("Cannot locate anchor boundary after source line {line}: "
              "anchor not present in realized content").format(line=source_line)
        )

    if len(matching_indices) > 1:
        if len(claimed_indices) == 1:
            return claimed_indices[0] + 1
        elif len(claimed_indices) == 0:
            raise AmbiguousAnchorError(
                _("Anchor ambiguity: source line {line} appears {count} times "
                  "in realized content but none are claimed").format(
                    line=source_line, count=len(matching_indices))
            )
        else:
            raise AmbiguousAnchorError(
                _("Anchor ambiguity: source line {line} claimed {count} times").format(
                    line=source_line, count=len(claimed_indices))
            )

    return matching_indices[0] + 1


def _sequence_matches_at_position(
    entries: list[RealizedEntry],
    position: int,
    sequence: list[bytes]
) -> bool:
    """Check if sequence matches entries starting at exact position.

    Args:
        entries: Realized entries
        position: Starting position to check (0-indexed)
        sequence: Normalized sequence to match

    Returns:
        True if sequence matches at position, False otherwise
    """
    if position + len(sequence) > len(entries):
        return False

    return all(
        normalize_line_endings(entries[position + i].content) == sequence[i]
        for i in range(len(sequence))
    )


def _find_sequence_nearby(
    entries: list[RealizedEntry],
    position: int,
    sequence: list[bytes],
    window: int = 20
) -> int | None:
    """Search for sequence within window after position.

    Args:
        entries: Realized entries
        position: Starting position for search window (0-indexed)
        sequence: Normalized sequence to find
        window: Number of positions to search after position

    Returns:
        Position where sequence was found, or None if not found
    """
    search_end = min(position + window, len(entries) - len(sequence) + 1)

    for check_pos in range(position + 1, search_end):
        if _sequence_matches_at_position(entries, check_pos, sequence):
            return check_pos

    return None


def _remove_sequence_at_position(
    entries: list[RealizedEntry],
    position: int,
    sequence: list[bytes]
) -> list[RealizedEntry]:
    """Remove sequence from entries at exact position.

    Args:
        entries: Realized entries
        position: Position where sequence starts (0-indexed)
        sequence: Sequence to remove (length determines how many entries removed)

    Returns:
        New list with sequence removed
    """
    return entries[:position] + entries[position + len(sequence):]


def _position_after_claimed_insertions_at_boundary(
    entries: list[RealizedEntry],
    position: int
) -> int:
    """Return the first position after contiguous claimed entries at boundary."""
    check_pos = position

    while check_pos < len(entries) and entries[check_pos].is_claimed:
        check_pos += 1

    return check_pos


def _suppress_at_boundary_strict(
    entries: list[RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes]
) -> list[RealizedEntry]:
    """Suppress forbidden sequence with strict enforcement for merge operations.

    This enforces absence constraints with two-phase checking:

    Phase 1: Exact boundary enforcement
    - If sequence matches at exact boundary: suppress it (remove from entries)
    - If sequence not at exact boundary: move to phase 2

    Phase 2: Conservative nearby ambiguity check
    - Search within a limited window after the boundary (20 entries)
    - If forbidden sequence appears nearby but not at exact boundary,
      this indicates structural displacement (e.g., presence constraint
      insertions pushed the deletion target away from its anchored position)
    - Raise MergeError rather than silently failing to delete displaced content

    This is not general fuzzy matching. It is a conservative structural safety
    check: the deletion content must be suppressed at the exact anchored boundary.
    Finding it nearby indicates the batch was created from a different file version
    where the deletion target was positioned differently.

    Used by: merge_batch() when merging into live working tree

    Args:
        entries: Realized entries
        position: Exact boundary position to check (0-indexed)
        forbidden_sequence: Sequence that must not appear at this position (normalized)

    Returns:
        Entries with sequence removed if found at exact position

    Raises:
        MergeError: If sequence appears nearby but not at exact boundary (displacement)
    """
    # Phase 1: Check exact match at boundary
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence
            )

    # Phase 2: Check for nearby displacement (conservative safety check)
    nearby_pos = _find_sequence_nearby(entries, position, forbidden_sequence, window=20)
    if nearby_pos is not None:
        raise MergeError(
            _("Batch was created from a different version of the file")
        )

    # Not found - already suppressed or never existed
    return entries


def _suppress_at_boundary_for_realization(
    entries: list[RealizedEntry],
    position: int,
    forbidden_sequence: list[bytes]
) -> list[RealizedEntry]:
    """Suppress forbidden sequence with lenient enforcement for content realization.

    This enforces absence constraints only when exact match exists at boundary:
    - If sequence matches at exact boundary: suppress it (remove from entries)
    - If sequence not at exact boundary: no-op (baseline may not have content there)

    Used by: _build_realized_content() when building display/storage content from
    baseline. The baseline may legitimately not have the deletion content at the
    expected anchor, or may not have it at all. We only suppress if there's an
    exact structural match.

    Args:
        entries: Realized entries
        position: Exact boundary position to check (0-indexed)
        forbidden_sequence: Sequence that must not appear at this position (normalized)

    Returns:
        Entries with sequence removed if found at exact position, otherwise unchanged
    """
    # Only suppress if exact match at boundary
    if _sequence_matches_at_position(entries, position, forbidden_sequence):
        return _remove_sequence_at_position(entries, position, forbidden_sequence)

    after_claimed_insertions = _position_after_claimed_insertions_at_boundary(
        entries,
        position
    )
    if after_claimed_insertions != position:
        if _sequence_matches_at_position(
            entries,
            after_claimed_insertions,
            forbidden_sequence
        ):
            return _remove_sequence_at_position(
                entries,
                after_claimed_insertions,
                forbidden_sequence
            )

    # Not at boundary - no-op, don't suppress
    # (Baseline might not have this content or it might be elsewhere)
    return entries


def merge_batch(
    batch_source_content: bytes,
    ownership: 'BatchOwnership',
    working_content: bytes
) -> bytes:
    """Constraint-based batch merge into working tree using structural provenance.

    This implements the architecture described in BATCHES.md:
    - Presence constraints: claimed lines must appear in result
    - Absence constraints: forbidden sequences must not appear at anchored boundaries
    - Structural provenance: track where each line came from in batch-source space
    - Bytes-based correctness: work with bytes throughout, no lossy decoding

    Algorithm:
    1. Normalize line endings to LF in bytes
    2. Resolve ownership into claimed lines and deletion claims
    3. Validate structural requirements (alignment, claimed region coherence)
    4. Apply presence constraints, building structured entries with provenance
    5. Apply absence constraints with exact boundary enforcement and nearby ambiguity check
    6. Emit final bytes content

    Presence constraints are satisfied by:
    - Keeping claimed lines if already present in working tree
    - Adding missing claimed lines at structurally appropriate positions
    - Tagging each realized entry with its batch-source line (if any)

    Absence constraints are satisfied by two-phase enforcement:
    - Phase 1: Check if forbidden sequence starts exactly at the anchored boundary
      - If yes: suppress it
      - If no: move to phase 2
    - Phase 2: Conservative nearby ambiguity check within limited window
      - If forbidden sequence found nearby (not at exact boundary):
        structural displacement detected, raise MergeError
      - If not found nearby: constraint already satisfied
    - If anchor boundary is ambiguous: raise MergeError

    The nearby check is not general fuzzy matching; it is a conservative structural
    safety check that detects when presence constraint insertions have displaced
    the deletion target, indicating the batch was created from a different file version.

    Args:
        batch_source_content: File content from batch source commit (bytes)
        ownership: BatchOwnership with presence and absence constraints
        working_content: Current working tree file content (bytes)

    Returns:
        New working tree content with constraints applied (bytes)

    Raises:
        MergeError: If constraints cannot be reliably satisfied or structural
                    displacement is detected
    """
    working_normalized = working_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    source_normalized = batch_source_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')

    source_lines = source_normalized.splitlines(keepends=True) if source_normalized else []
    working_lines = working_normalized.splitlines(keepends=True) if working_normalized else []

    resolved = ownership.resolve()
    claimed_line_set = resolved.claimed_line_set
    deletion_claims = resolved.deletion_claims

    mapping = match_lines(source_lines, working_lines)
    _check_structural_validity(
        mapping,
        claimed_line_set,
        deletion_claims,
        source_lines,
        working_lines
    )

    realized_entries = _satisfy_constraints(
        source_lines,
        working_lines,
        claimed_line_set,
        deletion_claims
    )

    return b"".join(entry.content for entry in realized_entries)


def discard_batch(
    batch_source_content: bytes,
    ownership: 'BatchOwnership',
    working_content: bytes,
    baseline_content: bytes
) -> bytes:
    """Constraint-based batch discard: structural inverse of merge_batch.

    This implements the inverse of merge_batch using the same constraint-based model:
    - Reverse presence constraints: remove/replace batch-owned claimed lines
    - Restore absence constraints: restore deleted sequences at anchored boundaries
    - Structural provenance: track where each line came from in batch-source space
    - Bytes-based correctness: work with bytes throughout, no lossy decoding

    Algorithm:
    1. Normalize line endings to LF in bytes
    2. Build structured entries from working tree with source provenance
    3. Reverse presence constraints: replace claimed lines with baseline or remove
    4. Restore absence constraints: insert deleted sequences at anchored boundaries
    5. Emit final bytes content

    Reverse presence constraints:
    - For each working tree entry corresponding to a claimed source line:
      - If baseline has a unique mapped line for that source line: replace with baseline
      - If baseline has no mapped line (batch-added content): remove entirely
      - If mapping is ambiguous: raise MergeError

    Restore absence constraints:
    - For each DeletionClaim(anchor_line=N, content_lines=[...]):
      - Find exact boundary "after source line N" in realized entries
      - If sequence is not present at boundary: insert it
      - If sequence is already present: no-op
      - If anchor not present: skip gracefully (claim not applicable)
      - If anchor is ambiguous: raise error (structural problem)

    This is the structural inverse of merge_batch: where merge applies constraints,
    discard reverses them.

    Args:
        batch_source_content: File content from batch source commit (bytes)
        ownership: BatchOwnership with presence and absence constraints
        working_content: Current working tree file content (bytes)
        baseline_content: File content from baseline commit (bytes)

    Returns:
        New working tree content with batch effects reversed (bytes)

    Raises:
        MergeError: If inverse operations cannot be reliably performed
    """
    working_normalized = working_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    source_normalized = batch_source_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    baseline_normalized = baseline_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')

    source_lines = source_normalized.splitlines(keepends=True) if source_normalized else []
    working_lines = working_normalized.splitlines(keepends=True) if working_normalized else []
    baseline_lines = baseline_normalized.splitlines(keepends=True) if baseline_normalized else []

    resolved = ownership.resolve()
    claimed_line_set = resolved.claimed_line_set
    deletion_claims = resolved.deletion_claims

    working_to_source = match_lines(source_lines, working_lines)

    correspondence = _build_baseline_correspondence(
        baseline_lines,
        source_lines
    )

    realized_entries = _build_realized_entries_for_discard(
        source_lines,
        working_lines,
        working_to_source
    )

    realized_entries = _reverse_presence_constraints(
        realized_entries,
        claimed_line_set,
        source_lines,
        baseline_lines,
        correspondence
    )

    realized_entries = _restore_absence_constraints(
        realized_entries,
        deletion_claims
    )

    return b"".join(entry.content for entry in realized_entries)


def _build_baseline_correspondence(
    baseline_lines: list[bytes],
    source_lines: list[bytes]
) -> BaselineCorrespondence:
    """Build restoration correspondence from source lines to baseline regions.

    This is a discard-specific helper that maps source lines to baseline restoration
    regions. Unlike match_lines() which performs conservative identity matching,
    this understands diff structure to determine what baseline content should be
    restored when batch-owned source content is discarded.

    Key distinction from match_lines:
    - match_lines: "which source lines are definitely present in working?"
    - This helper: "what baseline content restores each source position?"

    Uses difflib.SequenceMatcher for diff decomposition (acceptable here because
    we're building restoration regions, not performing identity matching).

    Region kinds:
    - EQUAL: unchanged lines → restored line-by-line
    - INSERT: source-only (batch added) → removed during discard
    - REPLACE_LINE_BY_LINE: changed region (same size) → restored line-by-line
    - REPLACE_BY_HUNK: changed region (different sizes) → restored as whole unit

    Replace regions are subdivided when possible:
    - If baseline and source have same number of lines: REPLACE_LINE_BY_LINE
    - If sizes differ: REPLACE_BY_HUNK (must restore entire baseline block)

    For by-hunk replace regions, discard requires full ownership:
    - If batch owns entire source-side region → restore entire baseline block
    - If batch owns only part → raise MergeError (partial discard not safe)

    Args:
        baseline_lines: Baseline file lines (bytes with newlines)
        source_lines: Batch source file lines (bytes with newlines)

    Returns:
        BaselineCorrespondence mapping source lines to restoration regions
    """
    matcher = difflib.SequenceMatcher(None, baseline_lines, source_lines)

    regions: list[BaselineRegion] = []
    line_to_region: dict[int, BaselineRegion] = {}
    next_region_id = 1

    for tag, base_start, base_end, src_start, src_end in matcher.get_opcodes():
        if tag == 'equal':
            region = BaselineRegion(
                source_start_line=src_start + 1,
                source_end_line=src_end,
                baseline_lines=list(baseline_lines[base_start:base_end]),
                kind=RegionKind.EQUAL,
                region_id=next_region_id
            )
            next_region_id += 1
            regions.append(region)

            for src_line in range(src_start + 1, src_end + 1):
                line_to_region[src_line] = region

        elif tag == 'insert':
            region = BaselineRegion(
                source_start_line=src_start + 1,
                source_end_line=src_end,
                baseline_lines=[],
                kind=RegionKind.INSERT,
                region_id=next_region_id
            )
            next_region_id += 1
            regions.append(region)

            for src_line in range(src_start + 1, src_end + 1):
                line_to_region[src_line] = region

        elif tag == 'replace':
            base_len = base_end - base_start
            src_len = src_end - src_start

            if base_len == src_len and base_len > 0:
                baseline_segment = baseline_lines[base_start:base_end]
                source_segment = source_lines[src_start:src_end]

                sub_mapping = match_lines(baseline_segment, source_segment)

                all_source_mapped = all(
                    sub_mapping.get_target_line_from_source_line(i + 1) is not None
                    for i in range(len(source_segment))
                )

                all_baseline_mapped = all(
                    sub_mapping.get_source_line_from_target_line(i + 1) is not None
                    for i in range(len(baseline_segment))
                )

                if all_source_mapped and all_baseline_mapped:
                    region = BaselineRegion(
                        source_start_line=src_start + 1,
                        source_end_line=src_end,
                        baseline_lines=list(baseline_segment),
                        kind=RegionKind.REPLACE_LINE_BY_LINE,
                        region_id=next_region_id
                    )
                else:
                    region = BaselineRegion(
                        source_start_line=src_start + 1,
                        source_end_line=src_end,
                        baseline_lines=list(baseline_segment),
                        kind=RegionKind.REPLACE_BY_HUNK,
                        region_id=next_region_id
                    )

                next_region_id += 1
                regions.append(region)

                for src_line in range(src_start + 1, src_end + 1):
                    line_to_region[src_line] = region

            else:
                region = BaselineRegion(
                    source_start_line=src_start + 1,
                    source_end_line=src_end,
                    baseline_lines=list(baseline_lines[base_start:base_end]),
                    kind=RegionKind.REPLACE_BY_HUNK,
                    region_id=next_region_id
                )
                next_region_id += 1
                regions.append(region)

                for src_line in range(src_start + 1, src_end + 1):
                    line_to_region[src_line] = region

    return BaselineCorrespondence(
        line_to_region=line_to_region,
        regions=regions
    )


def _build_realized_entries_for_discard(
    source_lines: list[bytes],
    working_lines: list[bytes],
    working_to_source: 'LineMapping'
) -> list[RealizedEntry]:
    """Build structured entries from working tree with source provenance.

    This creates a realized representation of the current working tree content,
    tagging each entry with its source-space provenance (if any). This allows
    subsequent discard operations to reason about which entries are batch-owned.

    Args:
        source_lines: Batch source lines (bytes with newlines)
        working_lines: Working tree lines (bytes with newlines)
        working_to_source: Mapping from source to working tree

    Returns:
        Realized entries representing working tree with source provenance
    """
    result: list[RealizedEntry] = []

    for working_idx, working_line in enumerate(working_lines):
        source_line = working_to_source.get_source_line_from_target_line(working_idx + 1)
        result.append(RealizedEntry(
            content=working_line,
            source_line=source_line,
            target_line=working_idx + 1,
            is_claimed=False
        ))

    return result


def _reverse_presence_constraints(
    entries: list[RealizedEntry],
    claimed_line_set: set[int],
    source_lines: list[bytes],
    baseline_lines: list[bytes],
    correspondence: BaselineCorrespondence
) -> list[RealizedEntry]:
    """Reverse presence constraints: replace/remove batch-owned claimed lines.

    For each entry in the working tree that corresponds to a claimed source line:
    - If from EQUAL or REPLACE_LINE_BY_LINE region: replace with baseline line-by-line
    - If from INSERT region: remove (batch-added content)
    - If from REPLACE_BY_HUNK region: verify full ownership, then restore as unit
    - If region is ambiguous: raise MergeError

    This is the inverse of presence constraint application: where merge ensures
    claimed lines are present, discard ensures they are removed or restored to baseline.

    Replace regions are handled intelligently:
    - Line-by-line replace (same size): restored line-by-line like equal regions
    - By-hunk replace (different sizes): requires full region ownership
      - If batch owns entire region → restore entire baseline block
      - If batch owns only part → raise MergeError (cannot safely discard partial)

    Args:
        entries: Realized entries from working tree with source provenance
        claimed_line_set: Set of source line numbers that are batch-owned
        source_lines: Batch source lines (for validation)
        baseline_lines: Baseline lines (not used directly; in correspondence)
        correspondence: Baseline restoration correspondence

    Returns:
        Entries with batch-owned claimed lines replaced or removed

    Raises:
        MergeError: If restoration is ambiguous or region not found
    """
    result: list[RealizedEntry] = []
    processed_replace_regions: set[int] = set()

    for entry in entries:
        if entry.source_line is not None and entry.source_line in claimed_line_set:
            region = correspondence.get_region_for_source_line(entry.source_line)

            if region is None:
                raise MergeError(
                    _("Cannot discard source line {line}: no baseline restoration region found").format(
                        line=entry.source_line
                    )
                )

            if region.is_ambiguous:
                raise MergeError(
                    _("Cannot discard source line {line}: baseline restoration is ambiguous").format(
                        line=entry.source_line
                    )
                )

            if region.kind in (RegionKind.EQUAL, RegionKind.REPLACE_LINE_BY_LINE):
                offset = entry.source_line - region.source_start_line
                if 0 <= offset < len(region.baseline_lines):
                    result.append(RealizedEntry(
                        content=region.baseline_lines[offset],
                        source_line=None,
                        is_claimed=False
                    ))
                else:
                    raise MergeError(
                        _("Source line {line} offset {offset} outside region bounds").format(
                            line=entry.source_line, offset=offset
                        )
                    )

            elif region.kind == RegionKind.INSERT:
                pass

            elif region.kind == RegionKind.REPLACE_BY_HUNK:
                if region.region_id not in processed_replace_regions:
                    source_lines_in_region = set(range(
                        region.source_start_line,
                        region.source_end_line + 1
                    ))
                    claimed_lines_in_region = source_lines_in_region & claimed_line_set

                    if claimed_lines_in_region != source_lines_in_region:
                        raise MergeError(
                            _("Cannot discard partial ownership of by-hunk replace region "
                              "(source lines {start}-{end}): batch owns {owned} of {total} lines").format(
                                start=region.source_start_line,
                                end=region.source_end_line,
                                owned=len(claimed_lines_in_region),
                                total=len(source_lines_in_region)
                            )
                        )

                    for baseline_line in region.baseline_lines:
                        result.append(RealizedEntry(
                            content=baseline_line,
                            source_line=None,
                            is_claimed=False
                        ))
                    processed_replace_regions.add(region.region_id)

            else:
                raise MergeError(
                    _("Unknown region kind: {kind}").format(kind=region.kind)
                )

        else:
            result.append(entry)

    return result


def _restore_absence_constraints(
    entries: list[RealizedEntry],
    deletion_claims: list['DeletionClaim']
) -> list[RealizedEntry]:
    """Restore absence constraints: insert deleted sequences at anchored boundaries.

    For each deletion claim, this function:
    1. Finds the exact boundary "after source line N" (or start-of-file)
    2. Checks if the deleted sequence is already present at that boundary
    3. If absent: inserts it at the exact boundary
    4. If present: no-op (already restored)
    5. If anchor not present: skip gracefully (claim not applicable)
    6. If anchor is ambiguous: raise error (structural problem)

    This is the inverse of absence constraint enforcement: where merge suppresses
    sequences at anchored boundaries, discard restores them.

    Anchor handling:
    - Missing anchor: Skip claim gracefully.
    - Ambiguous anchor: Raise AmbiguousAnchorError.

    Args:
        entries: Realized entries with source provenance
        deletion_claims: Absence constraints to restore

    Returns:
        Entries with deleted sequences restored at anchored boundaries

    Raises:
        AmbiguousAnchorError: If anchor boundary is ambiguous
        (MissingAnchorError is caught and skipped gracefully)
    """
    if not deletion_claims:
        return entries

    result = entries[:]

    for claim in deletion_claims:
        try:
            boundary = _find_boundary_after_source_line(result, claim.anchor_line)
        except MissingAnchorError:
            continue
        except AmbiguousAnchorError:
            raise

        if _sequence_present_at_boundary(result, boundary, claim.content_lines):
            continue

        restored_entries = [
            RealizedEntry(
                content=line,
                source_line=None,
                is_claimed=False
            )
            for line in claim.content_lines
        ]

        result = result[:boundary] + restored_entries + result[boundary:]

    return result


def _sequence_present_at_boundary(
    entries: list[RealizedEntry],
    boundary: int,
    sequence: list[bytes]
) -> bool:
    """Check if a byte sequence is present at the exact boundary position.

    Normalizes both entry content and sequence elements to LF line endings
    for consistent comparison across CRLF/LF representations.

    Args:
        entries: Realized entries
        boundary: Boundary position (0-indexed)
        sequence: Byte sequence to check for

    Returns:
        True if sequence is present at boundary, False otherwise
    """
    if boundary + len(sequence) > len(entries):
        return False

    return all(
        normalize_line_endings(entries[boundary + i].content) == normalize_line_endings(sequence[i])
        for i in range(len(sequence))
    )


def detect_ownership_conflicts(
    batch_source_content: bytes,
    ownerships: list['BatchOwnership']
) -> None:
    """Detect and raise error for conflicting ownership constraints.

    Checks for presence vs absence conflicts: when one batch claims a line
    must be present but another batch wants to delete it.

    Args:
        batch_source_content: The batch source content (bytes)
        ownerships: List of batch ownerships to check

    Raises:
        MergeError: If presence and absence constraints conflict
    """
    if len(ownerships) < 2:
        return

    source_normalized = batch_source_content.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    source_lines = source_normalized.splitlines(keepends=True)

    for i in range(len(ownerships)):
        for j in range(i + 1, len(ownerships)):
            _check_pair_conflicts(source_lines, ownerships[i], ownerships[j])


def _check_pair_conflicts(
    source_lines: list[bytes],
    ownership_a: 'BatchOwnership',
    ownership_b: 'BatchOwnership'
) -> None:
    """Check for conflicts between two ownerships with anchor awareness.

    This checks if presence and absence constraints actually conflict at their
    structural locations, not just if the content happens to match.

    A conflict occurs when:
    - Batch A claims line N (presence)
    - Batch B has deletion anchored such that it would suppress line N (absence)
    - The deletion content matches the claimed line

    Same content at different structural locations does not conflict.

    Args:
        source_lines: Batch source lines (bytes)
        ownership_a: First ownership
        ownership_b: Second ownership

    Raises:
        MergeError: If constraints conflict at same structural location
    """
    resolved_a = ownership_a.resolve()
    resolved_b = ownership_b.resolve()

    _check_presence_vs_absence_conflict(
        source_lines,
        resolved_a.claimed_line_set,
        resolved_b.deletion_claims
    )

    _check_presence_vs_absence_conflict(
        source_lines,
        resolved_b.claimed_line_set,
        resolved_a.deletion_claims
    )


def _check_presence_vs_absence_conflict(
    source_lines: list[bytes],
    claimed_lines: set[int],
    deletions: list['DeletionClaim']
) -> None:
    """Check if presence claims conflict with deletion claims.

    Args:
        source_lines: Batch source lines
        claimed_lines: Set of claimed line numbers
        deletions: List of deletion claims

    Raises:
        MergeError: If a claimed line would be suppressed by a deletion
    """
    for deletion in deletions:
        if not deletion.content_lines:
            continue

        anchor = deletion.anchor_line if deletion.anchor_line is not None else 0
        deletion_start = anchor + 1

        deletion_normalized = [
            line.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
            for line in deletion.content_lines
        ]

        for line_num in claimed_lines:
            if 1 <= line_num <= len(source_lines):
                if line_num >= deletion_start and line_num < deletion_start + len(deletion_normalized):
                    offset = line_num - deletion_start
                    source_normalized = source_lines[line_num - 1].replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                    if offset < len(deletion_normalized) and source_normalized == deletion_normalized[offset]:
                        try:
                            preview = source_lines[line_num - 1][:50].decode('utf-8', errors='replace').strip()
                        except Exception:
                            preview = str(source_lines[line_num - 1][:50])
                        raise MergeError(
                            _("Ownership conflict: line {line} is claimed by one batch "
                              "but would be deleted by another (content: {preview})").format(
                                line=line_num, preview=preview)
                        )
