"""Tests for exact claim-elided source alternatives."""

from git_stage_batch.batch.merge.merge import (
    merge_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.ownership.model import BatchOwnership


SOURCE_LINES = (
    b"base one\n"
    b"request one\n"
    b"base two\n"
    b"request two\n"
    b"completion\n"
    b"tail\n"
    b"base one\n"
    b"request one\n"
    b"base two\n"
    b"request two\n"
    b"tail\n"
).splitlines(keepends=True)
PREDECESSOR_LINES = b"base one\nbase two\ntail\n".splitlines(keepends=True)


def test_merge_replays_exact_multirange_presence_context_alternative():
    """Replay inserts both claims into their exact embedded-source gaps."""
    ownership = BatchOwnership.from_presence_lines(["8,10"], [])

    with merge_batch_from_line_sequences_as_buffer(
        SOURCE_LINES,
        ownership,
        PREDECESSOR_LINES,
    ) as result:
        assert result.to_bytes() == (
            b"base one\nrequest one\nbase two\nrequest two\ntail\n"
        )
