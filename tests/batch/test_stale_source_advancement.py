"""Tests for stale batch source detection and advancement."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership import (
    BaselineReference,
    BatchOwnership,
    DeletionClaim,
    ReplacementUnit,
    _advance_source_content_preserving_existing_presence,
    _advance_source_content_preserving_existing_presence_with_provenance,
    _remap_batch_ownership_with_source_line_map,
    detect_stale_batch_source_for_selection,
    merge_batch_ownership,
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


def test_detect_stale_batch_source_with_missing_deletion_anchor():
    """Deletion-only selections after file start need source refresh."""
    stale_lines = [
        LineEntry(id=1, kind='-', old_line_number=2, new_line_number=None,
                 text_bytes=b"old line", text="old line", source_line=None),
    ]

    assert detect_stale_batch_source_for_selection(stale_lines) is True


def test_detect_current_batch_source_with_file_start_deletion_anchor():
    """A missing deletion source line is valid before the first line."""
    current_lines = [
        LineEntry(id=1, kind='-', old_line_number=1, new_line_number=None,
                 text_bytes=b"old first", text="old first", source_line=None),
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
    old_ownership = BatchOwnership.from_presence_lines(["1-2"], [])

    # Remap to new source
    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Lines 1-2 in old source should map to lines 2-3 in new source
    assert new_ownership.presence_claims[0].source_lines == ["2-3"]
    assert new_ownership.deletions == []


def test_remap_deletion_anchors_to_new_source():
    """Test remapping of deletion claim anchors from old source to new source."""
    old_source = b"line one\nline two\nline three\n"
    new_source = b"new first line\nline one\nline two\nline three\n"

    # Original ownership has deletion anchored at line 2
    old_ownership = BatchOwnership.from_presence_lines(
        [],
        [
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

    old_ownership = BatchOwnership.from_presence_lines(
        [],
        [
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
    old_ownership = BatchOwnership.from_presence_lines(["2"], [])

    # Should fail because line 2 cannot be uniquely mapped
    with pytest.raises(ValueError, match="Cannot remap presence line"):
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
    old_ownership = BatchOwnership.from_presence_lines(
        [],
        [
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
    old_ownership = BatchOwnership.from_presence_lines(["1-2", "5-6"], [])

    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Lines 1-2 → 3-4, lines 5-6 → 7-8
    assert new_ownership.presence_claims[0].source_lines == ["3-4,7-8"]


def test_remap_preserves_deletion_content():
    """Test that deletion content is preserved during remapping."""
    old_source = b"line one\nline two\n"
    new_source = b"prefix\nline one\nline two\n"

    deletion_content = [b"deleted line 1\n", b"deleted line 2\n"]

    old_ownership = BatchOwnership.from_presence_lines(
        [],
        [
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


def test_remap_preserves_explicit_replacement_units():
    """Replacement metadata should follow claimed-line source remapping."""
    old_source = b"new value\nanchor\n"
    new_source = b"prefix\nnew value\nanchor\n"

    old_ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(anchor_line=2, content_lines=[b"old value\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    new_ownership = remap_batch_ownership_to_new_source(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source,
    )

    assert new_ownership.presence_claims[0].source_lines == ["2"]
    assert new_ownership.deletions[0].anchor_line == 3
    assert new_ownership.replacement_units == [
        ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
    ]


def test_merge_coalesces_overlapping_replacement_units_after_deduplication():
    """Deduplicated deletion claims should keep replacement metadata disjoint."""
    deletion = DeletionClaim(anchor_line=None, content_lines=[b"old value\n"])
    existing = BatchOwnership.from_presence_lines(
        ["1"],
        [deletion],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )
    new = BatchOwnership.from_presence_lines(
        ["2"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old value\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    merged = merge_batch_ownership(existing, new)

    assert merged.presence_claims[0].source_lines == ["1-2"]
    assert merged.deletions == [deletion]
    assert merged.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]


def test_merge_deduplicated_deletions_keeps_stronger_baseline_reference():
    """Deduplicated deletions should keep baseline metadata from new claims."""
    existing = BatchOwnership.from_presence_lines(
        [],
        [DeletionClaim(anchor_line=1, content_lines=[b"old value\n"])],
    )
    reference = BaselineReference(
        after_line=1,
        after_content=b"anchor",
        before_line=2,
        before_content=b"next",
        has_before_line=True,
    )
    new = BatchOwnership.from_presence_lines(
        [],
        [
            DeletionClaim(
                anchor_line=1,
                content_lines=[b"old value\n"],
                baseline_reference=reference,
            ),
        ],
    )

    merged = merge_batch_ownership(existing, new)

    assert merged.deletions == [
        DeletionClaim(
            anchor_line=1,
            content_lines=[b"old value\n"],
            baseline_reference=reference,
        )
    ]


def test_merge_ignores_boolean_replacement_unit_deletion_indices():
    """JSON booleans should not be accepted as deletion indexes."""
    new = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old one\n"]),
            DeletionClaim(anchor_line=None, content_lines=[b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[True]),
        ],
    )

    merged = merge_batch_ownership(
        BatchOwnership.from_presence_lines([], []),
        new,
    )

    assert merged.replacement_units == []


def test_advance_source_preserves_claimed_lines_missing_from_working_tree():
    """Previously discarded claimed lines remain available in advanced source."""
    old_source = b"owned one\nowned two\nremaining change\n"
    working_tree = b"remaining change\nnew later change\n"
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])

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
    assert remapped.presence_claims[0].source_lines == ["1-2"]


def test_advance_source_tracks_working_line_provenance_for_ambiguous_duplicates():
    """Synthesized source should remember working-line identity."""
    old_source = b"owned before\nsame\nsame\nowned after\n"
    working_tree = b"same\nsame\n"
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])

    advanced = _advance_source_content_preserving_existing_presence_with_provenance(
        old_source_content=old_source,
        working_content=working_tree,
        ownership=ownership,
    )

    assert advanced.content == b"owned before\nowned after\nsame\nsame\n"
    assert advanced.source_line_map == {
        1: 1,
        4: 2,
    }
    assert advanced.working_line_map == {
        1: 3,
        2: 4,
    }
