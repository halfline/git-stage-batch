"""Tests for discard-line replacement boundary selection."""

from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.line_matching.occurrence_index import (
    LinePayloadOccurrenceIndex,
)
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.commands.selection import discard_line_replacement
from git_stage_batch.commands.selection.discard_line_replacement import (
    _presence_is_explicit_span_prefix_with_blank_suffix,
)
from git_stage_batch.core.line_selection import LineRanges


def test_explicit_span_uses_nearest_following_two_sided_boundary() -> None:
    """A later line's nearer boundary outranks the first selected line's anchor."""
    after_heading = BaselineReference(
        after_line=46,
        after_content=b"\n",
        has_after_line=True,
        before_line=47,
        before_content=b"graphical details\n",
        has_before_line=True,
    )
    before_heading = BaselineReference(
        after_line=44,
        after_content=b"",
        has_after_line=True,
        before_line=45,
        before_content=b"## Graphical testing",
        has_before_line=True,
    )
    source_lines = [
        b"selected one\n",
        b"selected two\n",
        b"selected three\n",
        b"\n",
        b"```sh\n",
        b"command\n",
        b"```\n",
        b"\n",
        b"## Graphical testing\n",
        b"\n",
        b"graphical details\n",
    ]

    with MatcherWorkspace() as workspace:
        occurrences = LinePayloadOccurrenceIndex(workspace, source_lines)
        chosen = discard_line_replacement._nearest_following_explicit_boundary(
            [after_heading, before_heading],
            source_occurrences=occurrences,
            start_boundary=3,
        )

    assert chosen is before_heading


def test_explicit_span_prefix_accepts_only_trailing_blank_suffix() -> None:
    """A selected trailing separator may complete an exact explicit span."""
    owned = LineRanges.from_ranges(((2, 3),))
    explicit = LineRanges.from_ranges(((2, 4),))
    matches_prefix = _presence_is_explicit_span_prefix_with_blank_suffix

    assert matches_prefix(
        owned,
        explicit_presence=explicit,
        source_lines=[b"head\n", b"one\n", b"two\n", b"\n"],
    )
    assert not matches_prefix(
        owned,
        explicit_presence=explicit,
        source_lines=[b"head\n", b"one\n", b"two\n", b"payload\n"],
    )
def test_displaced_single_line_moves_before_its_old_after_boundary() -> None:
    """A peeled line before a replacement stays before that old replacement."""
    displaced = BaselineReference(
        after_line=3,
        after_content=b"Useful commands:",
        has_after_line=True,
        before_line=4,
        before_content=b"",
        has_before_line=True,
    )

    shifted = discard_line_replacement._boundary_before_reference_after(
        displaced,
        [b"results\n", b"\n", b"Useful commands:\n", b"\n"],
    )

    assert shifted == BaselineReference(
        after_line=2,
        after_content=b"\n",
        has_after_line=True,
        before_line=3,
        before_content=b"Useful commands:\n",
        has_before_line=True,
    )
