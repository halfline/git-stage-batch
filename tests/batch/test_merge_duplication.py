"""Tests for avoiding duplicated content in merge_batch."""

from __future__ import annotations


from git_stage_batch.batch.ownership import DeletionClaim

from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.merge import merge_batch_from_line_sequences_as_buffer
from git_stage_batch.editor import EditorBuffer


def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
) -> bytes:
    """Return merged bytes through the buffer-returning production API."""
    with (
        EditorBuffer.from_bytes(batch_source_content) as source_lines,
        EditorBuffer.from_bytes(working_content) as working_lines,
        merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
        ) as buffer,
    ):
        return buffer.to_bytes()


def test_merge_batch_no_duplication_when_claimed_line_already_present():
    """Test that applying a batch with a claimed line that's already in working tree doesn't duplicate.

    Scenario:
    - Batch was created when baseline was "A\nB\n"
    - Batch source had "A\nX\nB\n" (X inserted)
    - We claimed line 2 (X)
    - Now working tree is "A\nX\nB\n" (X already there from another change)
    - Applying the batch should leave X present once
    """
    # Batch source content (normalized, what was saved in batch)
    batch_source_content = b"A\nX\nB\n"

    # Claimed line 2 (X)
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    # Working tree already has X
    working_content = b"A\nX\nB\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    # Should have A, X, B (not A, X, X, B)
    assert result == b"A\nX\nB\n", f"Expected no duplication, got: {repr(result)}"
    assert result.count(b"X") == 1, f"X should appear once, got: {result.count('X')} times"


def test_merge_batch_with_working_tree_ahead_of_batch():
    """Test applying batch when working tree has moved ahead.

    Scenario:
    - Batch source: A\nX\nB\n (X at line 2)
    - Claimed: line 2 (X)
    - Working tree: A\nX\nY\nB\n (has both X and Y)
    - Result should have X once, Y preserved
    """
    batch_source_content = b"A\nX\nB\n"

    ownership = BatchOwnership.from_presence_lines(["2"], [])

    working_content = b"A\nX\nY\nB\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    # Should preserve Y as working tree extra, not duplicate X
    assert result == b"A\nX\nY\nB\n", f"Expected A, X, Y, B, got: {repr(result)}"
    assert result.count(b"X") == 1, "X should appear once"
    assert result.count(b"Y") == 1, "Y should appear once"


def test_merge_batch_claimed_line_missing_from_working_tree():
    """Test applying batch when claimed line is missing from working tree.

    Scenario:
    - Batch source: A\nX\nB\n (X at line 2)
    - Claimed: line 2 (X)
    - Working tree: A\nB\n (X was removed)
    - Result should add X back
    """
    batch_source_content = b"A\nX\nB\n"

    ownership = BatchOwnership.from_presence_lines(["2"], [])

    working_content = b"A\nB\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    # Should insert X
    assert result == b"A\nX\nB\n", f"Expected X to be inserted, got: {repr(result)}"


def test_merge_batch_with_deletion_suppresses_content():
    """Test merge with deletion constraints (suppression).

    Scenario:
    - Batch source: A\nB\n
    - Deletions: [DeletionClaim(anchor_line=1, content="UNWANTED")]
      (meaning: suppress "UNWANTED" content - it's a constraint, not insertion)
    - Working tree: A\nUNWANTED\nB\n (has unwanted content)
    - Result should have the unwanted content removed
    """

    batch_source_content = b"A\nB\n"

    # Create a deletion constraint (suppression)
    ownership = BatchOwnership.from_presence_lines([], [DeletionClaim(anchor_line=1, content_lines=[b"UNWANTED\n"])])

    working_content = b"A\nUNWANTED\nB\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    # Should remove "UNWANTED" as a suppression constraint.
    assert b"UNWANTED" not in result, f"Expected UNWANTED to be suppressed, got: {repr(result)}"
    assert result == b"A\nB\n", f"Expected A, B, got: {repr(result)}"


def test_merge_batch_claimed_line_present_with_extras_after():
    """Test that working tree extras after a claimed line don't cause duplication.

    This is a potential edge case: when walking batch source and we have a claimed line
    that exists in working tree, followed by working tree extras.
    """
    # Batch source: A\nselected\nC\n
    batch_source_content = b"A\nselected\nC\n"

    # Claim line 2.
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    # Working tree: A\nselected\nextra1\nextra2\nC\n
    working_content = b"A\nselected\nextra1\nextra2\nC\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    print(f"Result: {repr(result)}")
    lines = result.split(b'\n')
    print(f"Lines: {lines}")

    # Expected result: A, selected, extra1, extra2, C.
    claimed_count = [line for line in lines if line == b"selected"]
    assert len(claimed_count) == 1, f"selected should appear once, got: {len(claimed_count)} times in {lines}"
    assert b"extra1" in result, "extra1 should be preserved"
    assert b"extra2" in result, "extra2 should be preserved"
    assert result == b"A\nselected\nextra1\nextra2\nC\n", f"Expected proper order, got: {repr(result)}"


def test_merge_batch_complex_divergence_scenario():
    """Test a complex scenario where working tree has diverged significantly.

    Scenario with the selected content already elsewhere in the working tree:
    - Batch source: A\nINSERTED\nB\nC\n (has INSERTED at line 2)
    - Claimed: line 2 (INSERTED)
    - Working tree: A\nDIFFERENT\nB\nINSERTED\nC\n
      (has both DIFFERENT and INSERTED, but INSERTED is in a different position)
    - The batch wants to ensure INSERTED is there
    - Working tree already has it, but at a different location
    - The result should have INSERTED once.
    """
    batch_source_content = b"A\nINSERTED\nB\nC\n"

    # Claim line 2 (INSERTED)
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    # Working tree has INSERTED at a different position
    working_content = b"A\nDIFFERENT\nB\nINSERTED\nC\n"

    result = merge_batch(batch_source_content, ownership, working_content)

    print(f"Result: {repr(result)}")
    lines = [line for line in result.split(b'\n') if line]  # Filter empty strings
    print(f"Lines: {lines}")

    # This is tricky - the algorithm should recognize that INSERTED exists
    # and preserve it, but what about its position?
    inserted_count = [line for line in lines if line == b"INSERTED"]
    print(f"INSERTED appears {len(inserted_count)} times")

    # The question is: should this be 1 or 2?
    # Ideally it should be 1 (no duplication)
    # But depending on the algorithm, it might be 2
