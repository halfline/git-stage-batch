"""Mergeability probing for displayed batch file ownership units."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.line_selection import LineRanges
from ..core.text_lines import normalize_line_sequence_endings
from ..exceptions import MergeError
from ..utils.repository_buffers import load_working_tree_file_as_buffer
from . import merge as batch_merge
from .line_matching.match import match_lines
from .ownership import BatchOwnership
from .ownership_unit_rebuild import rebuild_ownership_from_units
from .ownership_unit_types import OwnershipUnit
from .ownership_unit_validation import validate_ownership_units
from .ownership_units import build_ownership_units_from_display_lines


@dataclass
class BatchFileMergeability:
    """Mergeability result for a rendered batch file."""

    mergeable_id_ranges: LineRanges
    units: list[OwnershipUnit]


def probe_batch_file_mergeability(
    *,
    file_path: str,
    ownership: BatchOwnership,
    display_lines: list[dict],
    batch_source_lines: Sequence[bytes],
) -> BatchFileMergeability:
    """Return mergeable display IDs and ownership units for batch display lines."""
    if not display_lines:
        return BatchFileMergeability(
            mergeable_id_ranges=LineRanges.empty(),
            units=[],
        )

    mergeable_id_range_parts: list[tuple[int, int]] = []
    source_match_lines = normalize_line_sequence_endings(batch_source_lines)
    working_tree_buffer = load_working_tree_file_as_buffer(file_path)
    with working_tree_buffer as working_tree_lines:
        working_match_lines = normalize_line_sequence_endings(working_tree_lines)
        with match_lines(
            source_match_lines,
            working_match_lines,
        ) as source_to_working_mapping:

            units = build_ownership_units_from_display_lines(
                ownership,
                display_lines,
            )

            # Check each ownership unit once. All lines in an atomic unit share
            # the same mergeability result.
            for unit in units:
                try:
                    validate_ownership_units([unit])
                    ownership_for_unit = rebuild_ownership_from_units([unit])
                    if ownership_for_unit.is_empty():
                        continue
                    if not batch_merge.can_merge_batch_from_line_sequences(
                        source_match_lines,
                        ownership_for_unit,
                        working_match_lines,
                        source_to_working_mapping=source_to_working_mapping,
                    ):
                        continue
                    mergeable_id_range_parts.extend(unit.display_line_ids.ranges())
                except (MergeError, ValueError, KeyError, Exception):
                    # Unit not mergeable - exclude all its lines.
                    pass

    return BatchFileMergeability(
        mergeable_id_ranges=LineRanges.from_ranges(mergeable_id_range_parts),
        units=units,
    )
