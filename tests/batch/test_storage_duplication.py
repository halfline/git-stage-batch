"""Tests for avoiding duplication in realized batch content."""

from __future__ import annotations

from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.storage import (
    _build_realized_buffer_from_lines,
    _build_realized_content_from_lines,
)
from git_stage_batch.editor import EditorBuffer


def _build_realized_content_from_bytes(
    base_content: bytes,
    batch_source_content: bytes,
    ownership: BatchOwnership,
) -> bytes:
    with (
        EditorBuffer.from_bytes(base_content) as base_lines,
        EditorBuffer.from_bytes(batch_source_content) as batch_source_lines,
        _build_realized_buffer_from_lines(
            base_lines,
            batch_source_lines,
            ownership,
        ) as result,
    ):
        return result.to_bytes()


def test_build_realized_content_no_duplication_when_claiming_moved_line():
    """Test that claiming a moved line doesn't duplicate it in realized content.

    Scenario: A line exists in base and is also added elsewhere in batch source.
    When we claim the added instance, it should appear once, not twice.
    """
    # Base has line "X" at position 2
    base_content = b"A\nX\nB\n"

    # Batch source moved "X" to position 1 and kept it at position 2
    # (or added a duplicate "X")
    batch_source_content = b"X\nA\nX\nB\n"

    # Claim line 1 (the moved/added X)
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    result_lines = result.decode().split('\n')

    # Should have: X (claimed), A (base), X (base), B (base)
    # The base copy of X should not be repeated.
    print(f"Result lines: {result_lines}")
    print(f"Result: {result}")

    # Count occurrences of "X"
    x_count = result_lines.count("X")
    assert x_count == 2, f"Expected 'X' to appear 2 times, but got {x_count} times: {result_lines}"


def test_build_realized_content_duplicate_line_claimed():
    """Test claiming one instance of a duplicated line."""
    # Base has two "X" lines
    base_content = b"A\nX\nB\nX\nC\n"

    # Batch source adds another "X" at the start
    batch_source_content = b"X\nA\nX\nB\nX\nC\n"

    # Claim line 1 (the added X)
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    result_lines = result.decode().split('\n')

    print(f"Result lines: {result_lines}")

    # Should have: X (claimed), A (base), X (base), B (base), X (base), C (base)
    # Total of 3 X's
    x_count = result_lines.count("X")
    assert x_count == 3, f"Expected 'X' to appear 3 times, but got {x_count} times: {result_lines}"


def test_build_realized_content_simple_insert():
    """Baseline test: simple insert of new line."""
    base_content = b"A\nB\n"
    batch_source_content = b"A\nNEW\nB\n"

    # Claim line 2 (NEW)
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    assert result == b"A\nNEW\nB\n"


def test_build_realized_content_from_lines_accepts_non_list_sequences(line_sequence):
    """Realized content construction accepts indexed byte-line sequences."""
    base_lines = line_sequence([b"A\n", b"B\n"])
    batch_source_lines = line_sequence([b"A\n", b"NEW\n", b"B\n"])
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    result = _build_realized_content_from_lines(
        base_lines,
        batch_source_lines,
        ownership,
    )

    assert result == b"A\nNEW\nB\n"


def test_build_realized_buffer_from_lines_returns_buffer():
    """Realized content can be rendered into a buffer."""
    base_content = b"A\r\nB\r\n"
    batch_source_content = b"A\r\nNEW\r\nB\r\n"
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    with _build_realized_buffer_from_lines(
        base_content.splitlines(keepends=True),
        batch_source_content.splitlines(keepends=True),
        ownership,
    ) as result:
        assert result.to_bytes() == b"A\r\nNEW\r\nB\r\n"
        assert result.is_mmap_backed


def test_build_realized_content_equal_block_with_unclaimed_insert():
    """Test that unclaimed inserts don't appear, and equal blocks work correctly."""
    # Base: A, B, C
    base_content = b"A\nB\nC\n"

    # Source: A, INSERTED, B, C (inserted between A and B)
    batch_source_content = b"A\nINSERTED\nB\nC\n"

    # Claim nothing (just see equal blocks)
    ownership = BatchOwnership.from_presence_lines([], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)

    # Should get base back unchanged since we didn't claim the insert
    assert result == b"A\nB\nC\n", f"Expected base unchanged, got: {result}"


def test_build_realized_content_equal_then_claimed_insert():
    """Test equal block followed by claimed insert."""
    # Base: A, B
    base_content = b"A\nB\n"

    # Source: A, B, NEW
    batch_source_content = b"A\nB\nNEW\n"

    # Claim line 3 (NEW)
    ownership = BatchOwnership.from_presence_lines(["3"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    lines = result.split(b'\n')

    # Should have A, B (from equal block), NEW (from insert, claimed)
    # B should not be duplicated.
    assert lines.count(b"B") == 1, f"B should appear once, got: {lines}"
    assert result == b"A\nB\nNEW\n", f"Expected A, B, NEW, got: {result}"
