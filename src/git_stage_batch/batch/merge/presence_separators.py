"""Track blank lines inserted while applying a batch."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ...core.line_selection import LineRangeBuilder, LineRanges
from ...core.text_lines import normalize_line_sequence_endings
from ..line_matching.line_range_view import LineRangeView
from ..line_matching.match import match_lines
from ..line_matching.match_workspace import MatcherWorkspace
from ..line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
    normalized_line_payload,
)
from ..line_matching.sequence_equality import line_slice_equals


def find_added_presence_separators(
    source_lines: Sequence[bytes],
    presence_lines: LineRanges,
    before_lines: Sequence[bytes],
    after_lines: Sequence[bytes],
    *,
    spool_dir: str | Path | None = None,
) -> LineRanges:
    """Return source blank lines inserted before selected text."""
    normalized_source = normalize_line_sequence_endings(source_lines)
    normalized_before = normalize_line_sequence_endings(before_lines)
    normalized_after = normalize_line_sequence_endings(after_lines)
    separators = LineRangeBuilder()

    with (
        MatcherWorkspace(spool_dir=spool_dir) as workspace,
        match_lines(
            normalized_before,
            normalized_after,
            spool_dir=spool_dir,
        ) as before_to_after,
    ):
        after_occurrences = LinePayloadOccurrenceIndex(
            workspace,
            normalized_after,
            normalize_payloads=False,
        )
        for range_start, range_end in presence_lines.ranges():
            separator_line = range_start - 1
            if (
                separator_line < 1
                or separator_line in presence_lines
                or normalized_line_payload(normalized_source[separator_line - 1])
            ):
                continue

            selected = LineRangeView(
                normalized_source,
                range_start - 1,
                range_end,
            )
            target_start: int | None = None
            for source_offset in range(len(selected)):
                content = selected[source_offset]
                if after_occurrences.occurrence_count(content) != 1:
                    continue
                target_index = next(after_occurrences.matching_line_indexes(content))
                candidate_start = target_index - source_offset
                if (
                    candidate_start < 1
                    or candidate_start + len(selected) > len(normalized_after)
                    or not line_slice_equals(
                        normalized_after,
                        candidate_start,
                        selected,
                    )
                ):
                    continue
                target_start = candidate_start
                break
            if target_start is None:
                continue

            target_separator_line = target_start
            if (
                normalized_after[target_start - 1]
                != normalized_source[separator_line - 1]
                or before_to_after.get_source_line_from_target_line(
                    target_separator_line
                )
                is not None
            ):
                continue
            separators.add_line(separator_line)

        after_occurrences.close()
    return separators.finish()
