"""Display-backed ownership unit construction."""

from __future__ import annotations

from collections.abc import Sequence

from .display import build_display_lines_from_batch_source_lines
from .ownership import (
    BatchOwnership,
    OwnershipUnit,
    build_ownership_units_from_display_lines,
)


def build_ownership_units_from_batch_source_lines(
    ownership: BatchOwnership,
    batch_source_lines: Sequence[bytes],
) -> list[OwnershipUnit]:
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
