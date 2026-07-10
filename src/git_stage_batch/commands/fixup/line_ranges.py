"""Suggest-fixup old-line range helpers."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
import sys

from ...core.models import LineLevelChange
from ...exceptions import exit_with_error
from ...i18n import _


@dataclass(frozen=True)
class SuggestFixupLineRange:
    """Old-file line range used for suggest-fixup history lookup."""

    min_line: int
    max_line: int


def require_hunk_old_line_range(
    line_changes: LineLevelChange,
    *,
    porcelain: bool,
) -> SuggestFixupLineRange:
    """Return the old-file line range for every changed hunk line."""
    old_line_numbers = [
        entry.old_line_number
        for entry in line_changes.lines
        if entry.old_line_number is not None
    ]
    if not old_line_numbers:
        if porcelain:
            sys.exit(1)
        exit_with_error(
            _("No old line numbers found in hunk (all lines may be additions).")
        )

    return SuggestFixupLineRange(
        min_line=min(old_line_numbers),
        max_line=max(old_line_numbers),
    )


def require_selected_old_line_range(
    line_changes: LineLevelChange,
    requested_ids: Collection[int],
) -> SuggestFixupLineRange:
    """Return the old-file line range for selected changed lines."""
    old_line_numbers = [
        entry.old_line_number
        for entry in line_changes.lines
        if entry.id in requested_ids and entry.old_line_number is not None
    ]
    if not old_line_numbers:
        exit_with_error(
            _(
                "No old line numbers found for specified lines "
                "(they may be newly added lines)."
            )
        )

    return SuggestFixupLineRange(
        min_line=min(old_line_numbers),
        max_line=max(old_line_numbers),
    )
