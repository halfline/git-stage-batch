"""Tests for stale batch source detection and advancement."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership import (
    BatchOwnership,
    DeletionClaim,
    _advance_source_content_preserving_existing_presence,
    _remap_batch_ownership_with_source_line_map,
    detect_stale_batch_source_for_selection,
    remap_batch_ownership_to_new_source,
    translate_lines_to_batch_ownership,
)
from git_stage_batch.core.models import LineEntry


def test_detect_stale_batch_source_with_none_source_lines():
    """Test detection of stale batch source when source_line is None."""
    # Lines with source_line=None indicate stale source
    stale_lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                 text_bytes=b"new line", text="new line", source_line=None),
    ]

    assert detect_stale_batch_source_for_selection(stale_lines) is True


def test_detect_current_batch_source_with_valid_source_lines():
    """Test detection passes when all source_lines are valid."""
    current_lines = [
        LineEntry(id=1, kind=' ', old_line_number=1, new_line_number=1,
                 text_bytes=b"context", text="context", source_line=1),
        LineEntry(id=2, kind='+', old_line_number=None, new_line_number=2,
                 text_bytes=b"addition", text="addition", source_line=2),
    ]

    assert detect_stale_batch_source_for_selection(current_lines) is False


def test_translate_fails_loudly_with_none_source_line():
    """Test that translation fails loudly instead of silently dropping None source_lines."""
    stale_lines = [
        LineEntry(id=1, kind='+', old_line_number=None, new_line_number=1,
                 text_bytes=b"new code", text="new code", source_line=None),
    ]

    with pytest.raises(ValueError, match="Batch source is stale"):
        translate_lines_to_batch_ownership(stale_lines)


def test_remap_claimed_lines_to_new_source():
    """Test remapping of claimed lines from old source to new source."""
    # Old source has 3 lines
    old_source = b"line one\nline two\nline three\n"

    # New source has 4 lines (added a line at the beginning)
    new_source = b"new first line\nline one\nline two\nline three\n"

    # Original ownership claims lines 1-2 in old source
    old_ownership = BatchOwnership(
        claimed_lines=["1-2"],
        deletions=[]
    )

    # Remap to new source
    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Lines 1-2 in old source should map to lines 2-3 in new source
    assert new_ownership.claimed_lines == ["2-3"]
    assert new_ownership.deletions == []


def test_remap_deletion_anchors_to_new_source():
    """Test remapping of deletion claim anchors from old source to new source."""
    old_source = b"line one\nline two\nline three\n"
    new_source = b"new first line\nline one\nline two\nline three\n"

    # Original ownership has deletion anchored at line 2
    old_ownership = BatchOwnership(
        claimed_lines=[],
        deletions=[
            DeletionClaim(anchor_line=2, content_lines=[b"deleted line\n"])
        ]
    )

    # Remap to new source
    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Anchor at line 2 in old source should map to line 3 in new source
    assert len(new_ownership.deletions) == 1
    assert new_ownership.deletions[0].anchor_line == 3
    assert new_ownership.deletions[0].content_lines == [b"deleted line\n"]


def test_remap_start_of_file_deletion_anchor():
    """Test that start-of-file deletion anchors (None) remain None after remapping."""
    old_source = b"line one\nline two\n"
    new_source = b"new first line\nline one\nline two\n"

    old_ownership = BatchOwnership(
        claimed_lines=[],
        deletions=[
            DeletionClaim(anchor_line=None, content_lines=[b"deleted at start\n"])
        ]
    )

    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Start-of-file anchor should remain None
    assert len(new_ownership.deletions) == 1
    assert new_ownership.deletions[0].anchor_line is None


def test_remap_fails_when_line_removed_in_new_source():
    """Test that remapping fails loudly when claimed line is removed in new source."""
    old_source = b"line one\nline two\nline three\n"
    # New source removed line two
    new_source = b"line one\nline three\n"

    # Claim line 2 in old source
    old_ownership = BatchOwnership(
        claimed_lines=["2"],
        deletions=[]
    )

    # Should fail because line 2 cannot be uniquely mapped
    with pytest.raises(ValueError, match="Cannot remap claimed line"):
        remap_batch_ownership_to_new_source(
            ownership=old_ownership,
            old_source_content=old_source,
            new_source_content=new_source
        )


def test_remap_fails_when_anchor_removed_in_new_source():
    """Test that remapping fails loudly when deletion anchor is removed in new source."""
    old_source = b"line one\nline two\nline three\n"
    new_source = b"line one\nline three\n"

    # Anchor deletion at line 2
    old_ownership = BatchOwnership(
        claimed_lines=[],
        deletions=[
            DeletionClaim(anchor_line=2, content_lines=[b"deleted\n"])
        ]
    )

    # Should fail because anchor line 2 cannot be uniquely mapped
    with pytest.raises(ValueError, match="Cannot remap deletion anchor"):
        remap_batch_ownership_to_new_source(
            ownership=old_ownership,
            old_source_content=old_source,
            new_source_content=new_source
        )


def test_remap_preserves_multiple_claimed_line_ranges():
    """Test that remapping preserves multiple claimed line ranges correctly."""
    old_source = b"a\nb\nc\nd\ne\nf\n"
    # Add two lines at the beginning
    new_source = b"x\ny\na\nb\nc\nd\ne\nf\n"

    # Claim lines 1-2 and 5-6 in old source
    old_ownership = BatchOwnership(
        claimed_lines=["1-2", "5-6"],
        deletions=[]
    )

    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Lines 1-2 → 3-4, lines 5-6 → 7-8
    assert new_ownership.claimed_lines == ["3-4,7-8"]


def test_remap_preserves_deletion_content():
    """Test that deletion content is preserved during remapping."""
    old_source = b"line one\nline two\n"
    new_source = b"prefix\nline one\nline two\n"

    deletion_content = [b"deleted line 1\n", b"deleted line 2\n"]

    old_ownership = BatchOwnership(
        claimed_lines=[],
        deletions=[
            DeletionClaim(anchor_line=1, content_lines=deletion_content)
        ]
    )

    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Content should be preserved exactly
    assert new_ownership.deletions[0].content_lines == deletion_content


def test_advance_source_preserves_claimed_lines_missing_from_working_tree():
    """Previously discarded claimed lines remain available in advanced source."""
    old_source = b"owned one\nowned two\nremaining change\n"
    working_tree = b"remaining change\nnew later change\n"
    ownership = BatchOwnership(
        claimed_lines=["1-2"],
        deletions=[]
    )

    new_source, source_line_map = _advance_source_content_preserving_existing_presence(
        old_source_content=old_source,
        working_content=working_tree,
        ownership=ownership,
    )
    remapped = _remap_batch_ownership_with_source_line_map(
        ownership,
        source_line_map,
    )

    assert new_source == b"owned one\nowned two\nremaining change\nnew later change\n"
    assert remapped.claimed_lines == ["1-2"]
