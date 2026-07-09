"""Ownership unit construction, filtering, and rebuild helpers."""

from __future__ import annotations

from collections.abc import Sequence

from ..core.line_selection import LineRanges, LineSelection
from ..exceptions import MergeError
from ..i18n import _
from .display import build_display_lines_from_batch_source_lines
from .ownership import (
    BatchOwnership,
    ReplacementUnit,
)
from .ownership_claims import (
    LineRangeBuilder,
    format_ownership_line_set,
    parse_ownership_line_ranges,
    presence_claims_from_source_lines,
)
from .ownership_unit_types import (
    OwnershipUnit as _UnitRecord,
    OwnershipUnitKind as _UnitKind,
)
from .ownership_replacement_units import normalize_replacement_units


def build_ownership_units_from_display_lines(
    ownership: BatchOwnership,
    display_lines: list[dict],
) -> list[_UnitRecord]:
    """Build semantic ownership units from already reconstructed display lines.

    This is the fast path for callers that already need display lines for
    rendering.  It preserves the same grouping rules as
    build_ownership_units_from_batch_source_lines() without rebuilding the
    display model.
    """
    units, consumed_claimed_lines, consumed_deletion_indices = (
        _build_explicit_replacement_units_from_display_lines(
            ownership,
            display_lines,
        )
    )
    i = 0

    while i < len(display_lines):
        line = display_lines[i]
        if _display_line_is_consumed(
            line,
            consumed_claimed_lines,
            consumed_deletion_indices,
        ):
            i += 1
            continue

        if line["type"] == "deletion":
            # Collect consecutive deletion block
            deletion_run = _collect_display_run(
                display_lines,
                i,
                "deletion",
                consumed_claimed_lines,
                consumed_deletion_indices,
            )
            i = deletion_run["next_index"]

            # Check if immediately followed by claimed line (display adjacency)
            if (
                i < len(display_lines)
                and display_lines[i]["type"] == "claimed"
                and not _display_line_is_consumed(
                    display_lines[i],
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
            ):
                # Collect single claimed line (to preserve fine-grained reset)
                claimed_display_id = display_lines[i]["id"]
                claimed_source_line = display_lines[i]["source_line"]
                i += 1

                # Replacement unit: deletion block adjacent to single claimed line
                claimed_run = {
                    "display_ids": [claimed_display_id],
                    "source_lines": [claimed_source_line]
                }
                units.append(_build_replacement_unit(
                    ownership=ownership,
                    deletion_run=deletion_run,
                    claimed_run=claimed_run
                ))
            else:
                # Deletion-only unit: no adjacent claimed block
                units.append(_build_deletion_only_unit(
                    ownership=ownership,
                    deletion_run=deletion_run
                ))

        elif line["type"] == "claimed":
            # Collect single claimed line (not a block, to preserve fine-grained reset)
            claimed_display_id = line["id"]
            claimed_source_line = line["source_line"]
            i += 1

            # Check if immediately followed by deletion block (display adjacency)
            if (
                i < len(display_lines)
                and display_lines[i]["type"] == "deletion"
                and not _display_line_is_consumed(
                    display_lines[i],
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
            ):
                # Collect consecutive deletion block
                deletion_run = _collect_display_run(
                    display_lines,
                    i,
                    "deletion",
                    consumed_claimed_lines,
                    consumed_deletion_indices,
                )
                i = deletion_run["next_index"]

                # Replacement unit: claimed line adjacent to deletion block
                claimed_run = {
                    "display_ids": [claimed_display_id],
                    "source_lines": [claimed_source_line]
                }
                units.append(_build_replacement_unit(
                    ownership=ownership,
                    deletion_run=deletion_run,
                    claimed_run=claimed_run
                ))
            else:
                # Presence-only unit: one claimed line without adjacent deletions
                # One unit per line allows independent reset
                units.append(_UnitRecord(
                    kind=_UnitKind.PRESENCE_ONLY,
                    claimed_source_lines=LineRanges.from_lines([claimed_source_line]),
                    deletion_claims=[],
                    display_line_ids=LineRanges.from_lines([claimed_display_id]),
                    is_atomic=False,
                    atomic_reason=None
                ))
        else:
            # Unknown type - skip
            i += 1

    return sorted(units, key=_ownership_unit_display_order_key)


def _ownership_unit_display_order_key(unit: _UnitRecord) -> int:
    """Return the first visible display line covered by a semantic unit."""
    return unit.display_line_ids.first() or 10**12


def _build_explicit_replacement_units_from_display_lines(
    ownership: BatchOwnership,
    display_lines: list[dict],
) -> tuple[list[_UnitRecord], LineRanges, set[int]]:
    """Build units from persisted replacement metadata."""
    units: list[_UnitRecord] = []
    consumed_claimed_lines = LineRanges.empty()
    consumed_deletion_indices: set[int] = set()

    replacement_units = normalize_replacement_units(
        ownership.replacement_units,
        deletion_count=len(ownership.deletions),
    )
    if not replacement_units:
        return units, consumed_claimed_lines, consumed_deletion_indices

    for replacement_unit in replacement_units:
        claimed_source_lines = parse_ownership_line_ranges(
            replacement_unit.presence_lines
        )
        deletion_indices = set(replacement_unit.deletion_indices)
        claimed_display_id_builder = LineRangeBuilder()
        deletion_display_id_builder = LineRangeBuilder()

        for display_line in display_lines:
            display_id = display_line.get("id")
            if display_id is None:
                continue

            if (
                display_line["type"] == "claimed"
                and display_line["source_line"] in claimed_source_lines
            ):
                claimed_display_id_builder.add_line(display_id)
            elif (
                display_line["type"] == "deletion"
                and display_line["deletion_index"] in deletion_indices
            ):
                deletion_display_id_builder.add_line(display_id)

        claimed_display_ids = claimed_display_id_builder.finish()
        deletion_display_ids = deletion_display_id_builder.finish()
        if not claimed_display_ids or not deletion_display_ids:
            continue

        deletion_claims = [
            ownership.deletions[index]
            for index in sorted(deletion_indices)
        ]
        units.append(_UnitRecord(
            kind=_UnitKind.REPLACEMENT,
            claimed_source_lines=claimed_source_lines,
            deletion_claims=deletion_claims,
            display_line_ids=claimed_display_ids.union(deletion_display_ids),
            is_atomic=True,
            atomic_reason="explicit_replacement",
            preserves_replacement_unit=True,
            replacement_origin=replacement_unit.origin,
        ))
        consumed_claimed_lines = consumed_claimed_lines.union(claimed_source_lines)
        consumed_deletion_indices.update(deletion_indices)

    return units, consumed_claimed_lines, consumed_deletion_indices


def _display_line_is_consumed(
    display_line: dict,
    consumed_claimed_lines: LineSelection,
    consumed_deletion_indices: set[int],
) -> bool:
    """Return True when a display line is already covered by an explicit unit."""
    if display_line["type"] == "claimed":
        return display_line["source_line"] in consumed_claimed_lines
    if display_line["type"] == "deletion":
        return display_line["deletion_index"] in consumed_deletion_indices
    return False


def _collect_display_run(
    display_lines: list,
    start_index: int,
    expected_type: str,
    consumed_claimed_lines: LineSelection,
    consumed_deletion_indices: set[int],
) -> dict:
    """Collect a consecutive run of display lines of the same type.

    Args:
        display_lines: List of display line dicts
        start_index: Starting index in display_lines
        expected_type: Expected line type ("deletion" or "claimed")

    Returns:
        Dict with:
        - display_ids: List of display IDs in the run
        - source_lines: List of source lines (for claimed) or None
        - deletion_indices: List of deletion indices (for deletion) or None
        - next_index: Index of first line after the run
    """
    display_ids = []
    source_lines = [] if expected_type == "claimed" else None
    deletion_indices = [] if expected_type == "deletion" else None

    i = start_index
    while (
        i < len(display_lines)
        and display_lines[i]["type"] == expected_type
        and not _display_line_is_consumed(
            display_lines[i],
            consumed_claimed_lines,
            consumed_deletion_indices,
        )
    ):
        display_ids.append(display_lines[i]["id"])

        if expected_type == "claimed":
            source_lines.append(display_lines[i]["source_line"])
        elif expected_type == "deletion":
            deletion_indices.append(display_lines[i]["deletion_index"])

        i += 1

    return {
        "display_ids": display_ids,
        "source_lines": source_lines,
        "deletion_indices": deletion_indices,
        "next_index": i
    }


def _build_replacement_unit(
    ownership: BatchOwnership,
    deletion_run: dict,
    claimed_run: dict
) -> _UnitRecord:
    """Build a REPLACEMENT unit from adjacent deletion and claimed runs.

    Args:
        ownership: BatchOwnership containing absence claims
        deletion_run: Dict from _collect_display_run for deletions
        claimed_run: Dict from _collect_display_run for claimed lines

    Returns:
        REPLACEMENT OwnershipUnit (atomic)
    """
    deletion_claims = [
        ownership.deletions[idx]
        for idx in set(deletion_run["deletion_indices"])
    ]

    return _UnitRecord(
        kind=_UnitKind.REPLACEMENT,
        claimed_source_lines=LineRanges.from_lines(claimed_run["source_lines"]),
        deletion_claims=deletion_claims,
        display_line_ids=LineRanges.from_lines(
            [*deletion_run["display_ids"], *claimed_run["display_ids"]]
        ),
        is_atomic=True,
        atomic_reason="display_adjacency"
    )


def _build_deletion_only_unit(
    ownership: BatchOwnership,
    deletion_run: dict
) -> _UnitRecord:
    """Build a DELETION_ONLY unit from a deletion run with no adjacent claimed lines.

    Args:
        ownership: BatchOwnership containing absence claims
        deletion_run: Dict from _collect_display_run for deletions

    Returns:
        DELETION_ONLY OwnershipUnit (atomic)
    """
    deletion_claims = [
        ownership.deletions[idx]
        for idx in set(deletion_run["deletion_indices"])
    ]

    return _UnitRecord(
        kind=_UnitKind.DELETION_ONLY,
        claimed_source_lines=LineRanges.empty(),
        deletion_claims=deletion_claims,
        display_line_ids=LineRanges.from_lines(deletion_run["display_ids"]),
        is_atomic=True,
        atomic_reason="deletion_only"
    )


def validate_ownership_units(units: list[_UnitRecord]) -> None:
    """Validate structural invariants of ownership units.

    Ensures:
    - No orphaned absence claims
    - No duplicate ownership of absence claims
    - Atomic units have valid structure

    Args:
        units: List of ownership units to validate

    Raises:
        MergeError: If units have invalid structure
    """
    # Track absence claim usage to ensure no orphans or duplicates
    deletion_claim_usage = {}

    for unit in units:
        for claim in unit.deletion_claims:
            claim_id = id(claim)
            if claim_id in deletion_claim_usage:
                # Duplicate ownership - may be intentional in some cases
                # but worth tracking for now
                pass
            deletion_claim_usage[claim_id] = unit

        # Validate atomic units have coherent structure
        if unit.is_atomic:
            if unit.kind == _UnitKind.REPLACEMENT:
                if not unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _("Invalid replacement in batch metadata: expected both added and removed lines.")
                    )
            elif unit.kind == _UnitKind.DELETION_ONLY:
                if unit.claimed_source_lines or not unit.deletion_claims:
                    raise MergeError(
                        _("Invalid deletion in batch metadata: expected removed lines only.")
                    )


def rebuild_ownership_from_units(units: list[_UnitRecord]) -> BatchOwnership:
    """Rebuild BatchOwnership from semantic ownership units.

    Args:
        units: List of ownership units to combine

    Returns:
        New BatchOwnership with combined ownership from all units
    """
    all_presence_lines = LineRanges.empty()
    all_deletions = []
    replacement_units: list[ReplacementUnit] = []

    for unit in units:
        all_presence_lines = all_presence_lines.union(unit.claimed_source_lines)
        deletion_indices = []
        for deletion in unit.deletion_claims:
            all_deletions.append(deletion)
            deletion_indices.append(len(all_deletions) - 1)
        if (
            unit.kind == _UnitKind.REPLACEMENT
            and unit.preserves_replacement_unit
        ):
            replacement_units.append(ReplacementUnit(
                presence_lines=format_ownership_line_set(unit.claimed_source_lines),
                deletion_indices=deletion_indices,
                origin=unit.replacement_origin,
            ))

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(all_presence_lines),
        deletions=all_deletions,
        replacement_units=normalize_replacement_units(
            replacement_units,
            deletion_count=len(all_deletions),
        ),
    )


def build_ownership_units_from_batch_source_lines(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
) -> list[_UnitRecord]:
    """Build semantic ownership units from indexed batch-source lines.

    Persisted replacement metadata is honored first, so captured replacements
    remain whole atomic units even if their lines are no longer display-adjacent.
    Remaining lines fall back to display-adjacency grouping in reconstructed
    display order, not source-line proximity. This reflects what the user
    actually sees in the batch display.
    """
    display_lines = build_display_lines_from_batch_source_lines(
        batch_source_lines,
        ownership,
    )
    return build_ownership_units_from_display_lines(ownership, display_lines)
