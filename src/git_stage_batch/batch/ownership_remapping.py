"""Batch ownership remapping across source-line spaces."""

from __future__ import annotations

from collections.abc import Sequence

from ..core.line_selection import LineSelection
from .lineage import BatchSourceLineage
from .line_mapping import LineMapping
from .match import match_lines
from .ownership import (
    AbsenceClaim,
    BatchOwnership,
    ReplacementUnit,
)
from .ownership_claims import (
    format_ownership_line_set,
    parse_ownership_line_ranges,
    presence_claims_from_source_lines,
)
from .ownership_replacement_units import normalize_replacement_units


def _remap_replacement_units(
    replacement_units: list[ReplacementUnit],
    *,
    map_claimed_line,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Remap explicit replacement-unit presence lines into a new source space."""
    remapped_units: list[ReplacementUnit] = []

    for unit in replacement_units:
        new_presence_lines: set[int] = set()
        for old_line_num in parse_ownership_line_ranges(unit.presence_lines):
            new_line_num = map_claimed_line(old_line_num)
            if new_line_num is None:
                raise ValueError(
                    f"Cannot remap replacement unit presence line {old_line_num} "
                    f"from old source to new source: no unique mapping found."
                )
            new_presence_lines.add(new_line_num)

        remapped_units.append(ReplacementUnit(
            presence_lines=format_ownership_line_set(new_presence_lines),
            deletion_indices=unit.deletion_indices,
            origin=unit.origin,
        ))

    return normalize_replacement_units(
        remapped_units,
        deletion_count=deletion_count,
    )


def _first_unmapped_line(
    line_selection: LineSelection,
    lineage: BatchSourceLineage,
) -> int | None:
    return lineage.first_unmapped_source_line(line_selection)


def _remap_replacement_units_with_lineage(
    replacement_units: list[ReplacementUnit],
    *,
    lineage: BatchSourceLineage,
    deletion_count: int,
) -> list[ReplacementUnit]:
    """Remap replacement-unit presence lines with refreshed source lineage."""
    remapped_units: list[ReplacementUnit] = []

    for unit in replacement_units:
        old_presence_lines = parse_ownership_line_ranges(unit.presence_lines)
        first_unmapped = _first_unmapped_line(old_presence_lines, lineage)
        if first_unmapped is not None:
            raise ValueError(
                f"Cannot remap replacement unit presence line {first_unmapped} "
                f"from old source to new source: no unique mapping found."
            )

        remapped_units.append(ReplacementUnit(
            presence_lines=format_ownership_line_set(
                lineage.translate_source_selection(old_presence_lines)
            ),
            deletion_indices=unit.deletion_indices,
            origin=unit.origin,
        ))

    return normalize_replacement_units(
        remapped_units,
        deletion_count=deletion_count,
    )


def remap_batch_ownership_to_new_source_lines(
    ownership: BatchOwnership,
    old_source_lines: Sequence[bytes],
    new_source_lines: Sequence[bytes],
) -> BatchOwnership:
    """Remap batch ownership between old and new source line sequences."""
    with match_lines(old_source_lines, new_source_lines) as mapping:
        return _remap_batch_ownership_with_mapping(ownership, mapping)


def _remap_batch_ownership_with_mapping(
    ownership: BatchOwnership,
    mapping: LineMapping,
) -> BatchOwnership:
    """Remap batch ownership using an existing old-to-new source mapping."""
    # Remap presence lines
    old_presence = ownership.presence_line_set()
    new_presence = set()

    for old_line_num in old_presence:
        new_line_num = mapping.get_target_line_from_source_line(old_line_num)
        if new_line_num is None:
            # Line cannot be mapped - fail loudly
            raise ValueError(
                f"Cannot remap presence line {old_line_num} from old source to new source: "
                f"no unique mapping found. This indicates the old line was removed or "
                f"significantly changed in the new source."
            )
        new_presence.add(new_line_num)

    # Remap deletion anchors
    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            # Start-of-file anchor remains None
            new_deletions.append(AbsenceClaim(
                anchor_line=None,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))
        else:
            # Remap anchor line
            new_anchor = mapping.get_target_line_from_source_line(deletion.anchor_line)
            if new_anchor is None:
                # Anchor cannot be mapped - fail loudly
                raise ValueError(
                    f"Cannot remap deletion anchor line {deletion.anchor_line} from old source "
                    f"to new source: no unique mapping found. This indicates the anchor line "
                    f"was removed or significantly changed in the new source."
                )
            new_deletions.append(AbsenceClaim(
                anchor_line=new_anchor,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))

    new_replacement_units = _remap_replacement_units(
        ownership.replacement_units,
        map_claimed_line=mapping.get_target_line_from_source_line,
        deletion_count=len(new_deletions),
    )

    new_presence_baseline_references = {}
    for old_line_num, reference in ownership.presence_baseline_references().items():
        new_line_num = mapping.get_target_line_from_source_line(old_line_num)
        if new_line_num is not None:
            new_presence_baseline_references[new_line_num] = reference

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            new_presence,
            new_presence_baseline_references,
        ),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )


def remap_batch_ownership_with_lineage(
    ownership: BatchOwnership,
    lineage: BatchSourceLineage,
) -> BatchOwnership:
    """Remap ownership using provenance from source refresh construction."""
    old_presence = ownership.presence_line_set()
    first_unmapped = _first_unmapped_line(old_presence, lineage)
    if first_unmapped is not None:
        raise ValueError(
            f"Cannot remap presence line {first_unmapped} from old source to new source: "
            f"no preserved source lineage found."
        )
    new_presence = lineage.translate_source_selection(old_presence)

    new_deletions = []
    for deletion in ownership.deletions:
        if deletion.anchor_line is None:
            new_deletions.append(AbsenceClaim(
                anchor_line=None,
                content_lines=deletion.content_lines,
                baseline_reference=deletion.baseline_reference,
            ))
            continue

        new_anchor = lineage.translate_source_line(deletion.anchor_line)
        if new_anchor is None:
            raise ValueError(
                f"Cannot remap deletion anchor line {deletion.anchor_line} from old source "
                f"to new source: no preserved source lineage found."
            )
        new_deletions.append(AbsenceClaim(
            anchor_line=new_anchor,
            content_lines=deletion.content_lines,
            baseline_reference=deletion.baseline_reference,
        ))

    new_replacement_units = _remap_replacement_units_with_lineage(
        ownership.replacement_units,
        lineage=lineage,
        deletion_count=len(new_deletions),
    )

    new_presence_baseline_references = {}
    for old_line_num, reference in ownership.presence_baseline_references().items():
        new_line_num = lineage.translate_source_line(old_line_num)
        if new_line_num is not None:
            new_presence_baseline_references[new_line_num] = reference

    return BatchOwnership(
        presence_claims=presence_claims_from_source_lines(
            new_presence,
            new_presence_baseline_references,
        ),
        deletions=new_deletions,
        replacement_units=new_replacement_units,
    )
