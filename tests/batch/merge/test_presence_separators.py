"""Tests for separator provenance recorded during replay."""

from git_stage_batch.batch.merge.presence_separators import (
    find_added_presence_separators,
)
from git_stage_batch.core.line_selection import LineRanges


def test_records_a_blank_added_with_selected_content() -> None:
    source = [b"keep\n", b"\n", b"saved\n"]

    assert find_added_presence_separators(
        source,
        LineRanges.from_lines((3,)),
        [b"keep\n"],
        source,
    ) == LineRanges.from_lines((2,))


def test_ignores_a_separator_that_was_already_present() -> None:
    source = [b"keep\n", b"\n", b"saved\n", b"\n", b"tail\n"]
    before = [b"keep\n", b"\n", b"\n", b"tail\n"]
    after = [b"keep\n", b"\n", b"saved\n", b"\n", b"tail\n"]

    assert not find_added_presence_separators(
        source,
        LineRanges.from_lines((3,)),
        before,
        after,
    )
