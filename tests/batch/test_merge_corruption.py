"""Tests for merge behavior when owned context differs from the working tree."""

from git_stage_batch.exceptions import MergeError

import pytest

from git_stage_batch.batch.merge import merge_batch
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim


def test_merge_corruption_simplified():
    """Owned context mismatches should not place deletions in the wrong location."""
    # Batch source has both parser_status and parser_include sections.
    # After parser_status closing paren, there's new argument, new set_defaults,
    # then parser_include section
    batch_source = b"""def setup():
    parser_status = Parser(
        name="status",
        aliases=["st"],
        help="Show status",
    )
    parser_status.add_argument(
        "--porcelain",
        action="store_true",
    )
    parser_status.set_defaults(func=lambda args: status(args.porcelain))

    # Section B: parser_include doesn't exist in working tree
    parser_include = Parser(
        name="include",
    )

    # Later content that IS shared
    return [parser_status, parser_include]
"""

    # Working tree has parser_status with a different set_defaults.
    working = b"""def setup():
    parser_status = Parser(
        name="status",
        aliases=["st"],
        help="Show status",
    )
    parser_status.set_defaults(func=lambda _: status())

    # Different content after parser_status
    return [parser_status]
"""

    # Count lines to get correct line numbers
    batch_lines = batch_source.split(b'\n')
    working_lines = working.split(b'\n')

    print("\n=== Batch source ===")
    for i, line in enumerate(batch_lines, 1):
        print(f"{i:3}: {line}")

    print("\n=== Working tree ===")
    for i, line in enumerate(working_lines, 1):
        print(f"{i:3}: {line}")

    # Batch ownership:
    # Claim lines 4-16 (from aliases through parser_include start)
    # This includes line 14 (blank line before Section B comment)
    # and line 15 (the Section B comment)
    # and line 16 (parser_include start)
    claimed = [str(i) for i in range(4, 17)]

    # Deletion: after line 6 (closing paren), delete old set_defaults
    # The old set_defaults is at line 7 in working tree
    deletions = [DeletionClaim(
        anchor_line=6,
        content_lines=[b"    parser_status.set_defaults(func=lambda _: status())\n"]
    )]

    ownership = BatchOwnership(claimed, deletions)

    # Apply merge - should raise MergeError due to context mismatch

    with pytest.raises(MergeError) as exc_info:
        merge_batch(batch_source, ownership, working)

    # Verify the error message is concise
    error_msg = str(exc_info.value)
    assert "different version" in error_msg.lower(), \
        f"Expected error about version mismatch, got: {error_msg}"

    print("\n=== Merge correctly raised error ===")
    print(f"Error: {error_msg}")
    print("\nThe merge detected that the batch was created from a different")
    print("file version than the working tree.")
