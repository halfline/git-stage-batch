"""Tests for merge-time validation and error handling.

Tests cover:
1. Line ending normalization in sequence matching
2. Missing vs ambiguous anchor distinction
3. Structural coherence checks for partial selections
"""

import pytest

from git_stage_batch.batch.merge import (
    merge_batch,
    discard_batch,
    _sequence_present_at_boundary,
    _find_boundary_after_source_line,
    RealizedEntry,
)
from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim
from git_stage_batch.exceptions import MergeError, MissingAnchorError, AmbiguousAnchorError


def test_sequence_present_normalizes_both_sides():
    """_sequence_present_at_boundary() should normalize both entry content and sequence."""
    # Create entries with CRLF
    entries = [
        RealizedEntry(content=b"line 1\r\n", source_line=1, is_claimed=True),
        RealizedEntry(content=b"line 2\r\n", source_line=2, is_claimed=True),
        RealizedEntry(content=b"line 3\n", source_line=3, is_claimed=False),
    ]

    # Sequence with LF should match entry with CRLF
    sequence_lf = [b"line 1\n", b"line 2\n"]
    assert _sequence_present_at_boundary(entries, 0, sequence_lf) is True

    # Sequence with CRLF should match entry with LF
    sequence_crlf = [b"line 3\r\n"]
    assert _sequence_present_at_boundary(entries, 2, sequence_crlf) is True

    # Non-matching content should still return False
    sequence_wrong = [b"wrong line\n"]
    assert _sequence_present_at_boundary(entries, 0, sequence_wrong) is False


def test_discard_missing_anchor_skipped_gracefully():
    """Discard should skip deletion claims when anchor is missing (not raise)."""
    batch_source = b"""line 1
line 2
line 3
"""

    working = b"""different line 1
different line 2
different line 3
"""

    # Baseline is same as working (no changes yet)
    baseline = working

    # Claim line 2, delete line after line 2
    # But line 2 doesn't exist in working tree (all lines different)
    ownership = BatchOwnership(
        claimed_lines=["2"],
        deletions=[DeletionClaim(
            anchor_line=2,
            content_lines=[b"old content\n"]
        )]
    )

    # Should complete without error (skips deletion claim gracefully)
    # because anchor line 2 is not present in working tree
    result = discard_batch(batch_source, ownership, working, baseline)

    # Should restore baseline content (different lines)
    assert b"different line 2" in result


def test_find_boundary_missing_anchor_raises_specific_error():
    """_find_boundary_after_source_line raises MissingAnchorError when anchor not present."""
    # Entries without source line 5
    entries = [
        RealizedEntry(content=b"line 1\n", source_line=1, is_claimed=False),
        RealizedEntry(content=b"line 2\n", source_line=2, is_claimed=False),
        RealizedEntry(content=b"line 3\n", source_line=3, is_claimed=False),
    ]

    # Try to find boundary after source line 5 (not present)
    with pytest.raises(MissingAnchorError) as exc_info:
        _find_boundary_after_source_line(entries, 5)

    assert "not present" in str(exc_info.value).lower()


def test_find_boundary_ambiguous_anchor_raises_specific_error():
    """_find_boundary_after_source_line raises AmbiguousAnchorError for duplicates without claim."""
    # Entries with duplicate source line 2 (neither claimed)
    entries = [
        RealizedEntry(content=b"line 1\n", source_line=1, is_claimed=False),
        RealizedEntry(content=b"line 2\n", source_line=2, is_claimed=False),
        RealizedEntry(content=b"line 2\n", source_line=2, is_claimed=False),  # Duplicate
        RealizedEntry(content=b"line 3\n", source_line=3, is_claimed=False),
    ]

    # Try to find boundary after source line 2 (ambiguous - two unclaimed occurrences)
    with pytest.raises(AmbiguousAnchorError) as exc_info:
        _find_boundary_after_source_line(entries, 2)

    assert "ambiguity" in str(exc_info.value).lower()


def test_find_boundary_uses_claimed_when_duplicates_exist():
    """_find_boundary_after_source_line uses claimed occurrence when anchor duplicated."""
    # Entries with duplicate source line 2, but one is claimed
    entries = [
        RealizedEntry(content=b"line 1\n", source_line=1, is_claimed=False),
        RealizedEntry(content=b"line 2\n", source_line=2, is_claimed=False),
        RealizedEntry(content=b"line 2\n", source_line=2, is_claimed=True),  # Claimed one
        RealizedEntry(content=b"line 3\n", source_line=3, is_claimed=False),
    ]

    # Should use the claimed occurrence (index 2) and return boundary after it (index 3)
    boundary = _find_boundary_after_source_line(entries, 2)
    assert boundary == 3  # After the claimed occurrence


def test_partial_selection_corruption_still_caught():
    """Partial line selection from incompatible region should raise MergeError.

    This is the core corruption case: selecting only additions (lines 5-10)
    without the deletion (line 4) from a batch created from a different
    file version with incompatible trailing context.
    """
    # Batch source: has both parser_status and parser_include sections
    batch_source = b"""def setup():
    parser_status = Parser(
        name="status",
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
    parser_include.add_argument(
        "--file",
    )

    # Later content that IS shared
    return [parser_status, parser_include]
"""

    # Working tree: has parser_status with OLD set_defaults
    # After parser_status, has DIFFERENT content (no parser_include)
    working = b"""def setup():
    parser_status = Parser(
        name="status",
        help="Show status",
    )
    parser_status.set_defaults(func=lambda _: status())

    # Different content after parser_status
    return [parser_status]
"""

    # Claim lines 7-10 (the additions in parser_status section)
    # This includes the new argument and new set_defaults
    # But it's part of a region whose trailing context (parser_include) doesn't exist
    claimed = [str(i) for i in range(7, 11)]
    ownership = BatchOwnership(claimed_lines=claimed, deletions=[])

    # Should raise MergeError due to incompatible trailing context
    with pytest.raises(MergeError) as exc_info:
        merge_batch(batch_source, ownership, working)

    assert "different version" in str(exc_info.value).lower()


def test_safe_partial_selection_succeeds():
    """Partial selection with coherent surrounding structure should succeed."""
    # Batch source
    batch_source = b"""line 1
line 2
line 3
line 4
line 5
line 6
line 7
"""

    # Working tree: same structure, just missing the middle lines
    working = b"""line 1
line 2
line 6
line 7
"""

    # Claim lines 3-5 (the additions)
    # The surrounding context is coherent: line 2 before, line 6 after
    # Both are present in working tree
    claimed = ["3", "4", "5"]
    ownership = BatchOwnership(claimed_lines=claimed, deletions=[])

    # Should succeed without error
    result = merge_batch(batch_source, ownership, working)

    # Should have all lines
    assert b"line 3" in result
    assert b"line 4" in result
    assert b"line 5" in result


def test_safe_partial_selection_with_mapped_trailing_context():
    """Partial selection succeeds when trailing context is mapped.

    Even if some claimed lines are missing from working tree, if the
    trailing context after the claimed run is mapped to the working tree,
    the structure is coherent and merge should succeed.
    """
    # Batch source with clear structure
    batch_source = b"""header 1
header 2
new addition 1
new addition 2
new addition 3
footer 1
footer 2
"""

    # Working tree: has headers and footers, missing additions
    working = b"""header 1
header 2
footer 1
footer 2
"""

    # Claim the additions (lines 3-5)
    # These are missing from working tree, BUT
    # the trailing context (footer 1, footer 2) IS mapped
    claimed = ["3", "4", "5"]
    ownership = BatchOwnership(claimed_lines=claimed, deletions=[])

    # Should succeed: trailing context is mapped
    result = merge_batch(batch_source, ownership, working)

    # Should have the additions inserted
    assert b"new addition 1" in result
    assert b"new addition 2" in result
    assert b"new addition 3" in result
    # And preserved footers
    assert b"footer 1" in result
    assert b"footer 2" in result


def test_append_only_interleaved_batch_first_application_succeeds():
    """One-sided anchoring is safe when applying into an empty target tail."""
    batch_source = b"""Header
Added line 1
Added line 2
Added line 3
Added line 4
"""

    working = b"Header\n"

    ownership = BatchOwnership(claimed_lines=["2", "4"], deletions=[])

    result = merge_batch(batch_source, ownership, working)

    assert result == b"Header\nAdded line 1\nAdded line 3\n"


def test_replacement_with_unmapped_trailing_source_succeeds_when_deletion_anchored():
    """A same-anchor deletion makes a one-sided replacement structurally safe."""
    batch_source = b"""# Test Project

A test project for git-stage-batch.

## Features
- Line-level staging
- Batch operations
"""

    working = b"""# Test Project

A test project.
"""

    ownership = BatchOwnership(
        claimed_lines=["3-4"],
        deletions=[DeletionClaim(
            anchor_line=2,
            content_lines=[b"A test project.\n"]
        )],
    )

    result = merge_batch(batch_source, ownership, working)

    assert result == b"""# Test Project

A test project for git-stage-batch.

"""


def test_crlf_normalization_in_discard_restoration():
    """Discard should handle CRLF in deletion content correctly.

    When deletion claim content uses CRLF but the sequence check uses LF,
    the normalization should allow correct matching without duplication.
    """
    # Batch source with LF
    batch_source = b"line 1\nline 2\nline 3\n"

    # Working tree: batch was applied (line 2 added, old content removed)
    working = b"line 1\nline 2\nline 3\n"

    # Baseline: original state before batch (had old content, not line 2)
    baseline = b"line 1\nold content\nline 3\n"

    # Claim line 2 (batch added it), deletion after line 1 with CRLF
    ownership = BatchOwnership(
        claimed_lines=["2"],
        deletions=[DeletionClaim(
            anchor_line=1,
            content_lines=[b"old content\r\n"]  # CRLF in deletion content
        )]
    )

    # Discard: should remove batch-owned line 2, restore "old content"
    # The CRLF in deletion content should normalize to LF for matching
    result = discard_batch(batch_source, ownership, working, baseline)

    # Should have baseline structure: line 1, old content, line 3
    assert b"line 1\n" in result
    assert b"old content\n" in result
    assert b"line 3\n" in result
    # Should not have line 2 (batch-owned, removed during discard)
    assert result.count(b"line 2") == 0
    # Should have exactly one "old content" (restored, not duplicated)
    assert result.count(b"old content") == 1


def test_incompatible_region_with_small_trailing_gap():
    """Small trailing gap (< 3 lines) should not trigger structural check.

    This ensures the threshold is not too aggressive and allows reasonable
    partial selections.
    """
    # Batch source
    batch_source = b"""line 1
line 2
claimed 1
claimed 2
trailing 1
trailing 2
line 3
"""

    # Working tree: missing claimed and trailing, but trailing gap is only 2 lines
    working = b"""line 1
line 2
line 3
"""

    # Claim lines 3-4
    claimed = ["3", "4"]
    ownership = BatchOwnership(claimed_lines=claimed, deletions=[])

    # Should succeed: trailing gap is only 2 unmapped lines (below threshold)
    result = merge_batch(batch_source, ownership, working)

    assert b"claimed 1" in result
    assert b"claimed 2" in result
