"""Tests for stale batch source detection and advancement."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership import (
    BaselineReference,
    BatchOwnership,
    DeletionClaim,
    ReplacementUnit,
    _advance_source_lines_preserving_existing_presence,
    _remap_batch_ownership_with_source_line_map,
    detect_stale_batch_source_for_selection,
    merge_batch_ownership,
    remap_batch_ownership_to_new_source_lines,
    translate_lines_to_batch_ownership,
)
from git_stage_batch.batch.lineage import _BatchSourceLineage, _LineageRun
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.core.models import LineEntry
from git_stage_batch.editor import EditorBuffer


class _IterationGuardedLineSelection:
    """Line selection that rejects full expansion in stale-source tests."""

    def __init__(self, ranges: tuple[tuple[int, int], ...]) -> None:
        self._ranges = ranges

    def __contains__(self, line_number: object) -> bool:
        if type(line_number) is not int:
            return False
        return any(start <= line_number <= end for start, end in self._ranges)

    def __bool__(self) -> bool:
        return bool(self._ranges)

    def __iter__(self):
        raise AssertionError("line selection should not be expanded")

    def ranges(self) -> tuple[tuple[int, int], ...]:
        return self._ranges


def _remap_ownership_from_content(
    *,
    ownership: BatchOwnership,
    old_source_content: bytes,
    new_source_content: bytes,
) -> BatchOwnership:
    with (
        EditorBuffer.from_bytes(old_source_content) as old_source_lines,
        EditorBuffer.from_bytes(new_source_content) as new_source_lines,
    ):
        return remap_batch_ownership_to_new_source_lines(
            ownership=ownership,
            old_source_lines=old_source_lines,
            new_source_lines=new_source_lines,
        )


def _advance_source_from_content(
    *,
    old_source_buffer: bytes,
    working_buffer: bytes,
    ownership: BatchOwnership,
):
    with (
        EditorBuffer.from_bytes(old_source_buffer) as old_source_lines,
        EditorBuffer.from_bytes(working_buffer) as working_lines,
    ):
        return _advance_source_lines_preserving_existing_presence(
            old_lines=old_source_lines,
            working_lines=working_lines,
            ownership=ownership,
        )


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
    new_ownership = _remap_ownership_from_content(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source
    )

    # Lines 1-2 in old source should map to lines 2-3 in new source
    assert new_ownership.presence_claims[0].source_lines == ["2-3"]
    assert new_ownership.deletions == []


def test_remap_claimed_lines_accepts_non_list_line_sequences(line_sequence):
    """Ownership remapping accepts indexed byte-line sequences."""
    old_lines = line_sequence([b"line one\n", b"line two\n", b"line three\n"])
    new_lines = line_sequence([
        b"new first line\n",
        b"line one\n",
        b"line two\n",
        b"line three\n",
    ])
    old_ownership = BatchOwnership.from_presence_lines(["1-2"], [])

    new_ownership = remap_batch_ownership_to_new_source_lines(
        ownership=old_ownership,
        old_source_lines=old_lines,
        new_source_lines=new_lines,
    )

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
    new_ownership = _remap_ownership_from_content(
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

    new_ownership = _remap_ownership_from_content(
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
        _remap_ownership_from_content(
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
        _remap_ownership_from_content(
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

    new_ownership = _remap_ownership_from_content(
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

    new_ownership = _remap_ownership_from_content(
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

    new_ownership = _remap_ownership_from_content(
        ownership=old_ownership,
        old_source_content=old_source,
        new_source_content=new_source,
    )

    assert new_ownership.presence_claims[0].source_lines == ["2"]
    assert new_ownership.deletions[0].anchor_line == 3
    assert new_ownership.replacement_units == [
        ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
    ]


def test_batch_source_lineage_translates_ranges():
    """Batch source lineage should translate selections without per-line storage."""
    with _BatchSourceLineage.from_runs(
        source_runs=[
            _LineageRun(old_start=1, old_end=2, new_start=10),
            _LineageRun(old_start=3, old_end=4, new_start=12),
            _LineageRun(old_start=8, old_end=9, new_start=20),
        ],
        working_runs=[
            _LineageRun(old_start=20, old_end=21, new_start=30),
        ],
    ) as lineage:
        assert tuple(lineage.source_runs()) == (
            _LineageRun(old_start=1, old_end=4, new_start=10),
            _LineageRun(old_start=8, old_end=9, new_start=20),
        )
        assert lineage.translate_source_line(3) == 12
        assert lineage.translate_source_line(7) is None
        assert lineage.translate_source_selection(
            LineRanges.from_ranges([(2, 8)])
        ).ranges() == ((11, 13), (20, 20))
        assert lineage.translate_working_line(21) == 31
        assert lineage.translate_working_selection(
            LineRanges.from_ranges([(20, 21)])
        ).ranges() == ((30, 31),)


def test_batch_source_lineage_finds_unmapped_source_ranges():
    """Unmapped-source lookup should scan runs without expanding selections."""
    with _BatchSourceLineage.from_runs(
        source_runs=[
            _LineageRun(old_start=1, old_end=20, new_start=100),
            _LineageRun(old_start=30, old_end=40, new_start=200),
        ],
    ) as lineage:
        assert lineage.first_unmapped_source_line(
            _IterationGuardedLineSelection(((5, 5), (10, 12)))
        ) is None
        assert lineage.first_unmapped_source_line(
            _IterationGuardedLineSelection(((5, 5), (10, 12), (25, 26)))
        ) == 25


def test_batch_source_lineage_rejects_overlapping_appends():
    """Lineage appends should require monotonic old-coordinate runs."""
    with _BatchSourceLineage() as lineage:
        lineage.append_source_run(
            _LineageRun(old_start=10, old_end=20, new_start=100)
        )

        with pytest.raises(ValueError, match="lineage runs must not overlap"):
            lineage.append_source_run(
                _LineageRun(old_start=5, old_end=9, new_start=200)
            )

        with pytest.raises(ValueError, match="lineage runs must not overlap"):
            lineage.append_source_run(
                _LineageRun(old_start=20, old_end=25, new_start=300)
            )


def test_batch_source_lineage_closes_mapped_storage():
    """Batch source lineage should close mapped run storage."""
    lineage = _BatchSourceLineage.from_runs(
        source_runs=[
            _LineageRun(old_start=1, old_end=1000, new_start=1),
            _LineageRun(old_start=2000, old_end=2000, new_start=5000),
        ],
    )

    assert lineage.byte_count > 0

    lineage.close()

    assert lineage.closed is True
    assert lineage.byte_count == 0
    with pytest.raises(ValueError, match="batch source lineage is closed"):
        lineage.translate_source_line(1)


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
    """Previously discarded claimed lines remain available in refreshed source."""
    old_source = b"owned one\nowned two\nremaining change\n"
    working_tree = b"remaining change\nnew later change\n"
    ownership = BatchOwnership.from_presence_lines(["1-2"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        remapped = _remap_batch_ownership_with_source_line_map(
            ownership,
            source_with_provenance.source_line_map,
        )

        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned one\nowned two\nremaining change\nnew later change\n"
        )
    assert remapped.presence_claims[0].source_lines == ["1-2"]


def test_advance_source_tracks_working_line_provenance_for_ambiguous_duplicates():
    """Synthesized source should remember working-line identity."""
    old_source = b"owned before\nsame\nsame\nowned after\n"
    working_tree = b"same\nsame\n"
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])

    with _advance_source_from_content(
        old_source_buffer=old_source,
        working_buffer=working_tree,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned before\nowned after\nsame\nsame\n"
        )
        assert source_with_provenance.source_line_map == {
            1: 1,
            4: 2,
        }
        assert source_with_provenance.working_line_map == {
            1: 3,
            2: 4,
        }

def test_advance_source_lines_accepts_non_list_line_sequences(line_sequence):
    """Source construction accepts indexed line sequences."""
    old_lines = line_sequence([
        b"owned before\n",
        b"same\n",
        b"same\n",
        b"owned after\n",
    ])
    working_lines = line_sequence([b"same\n", b"same\n"])
    ownership = BatchOwnership.from_presence_lines(["1,4"], [])

    with _advance_source_lines_preserving_existing_presence(
        old_lines=old_lines,
        working_lines=working_lines,
        ownership=ownership,
    ) as source_with_provenance:
        assert source_with_provenance.source_buffer.to_bytes() == (
            b"owned before\nowned after\nsame\nsame\n"
        )
        assert source_with_provenance.source_line_map == {
            1: 1,
            4: 2,
        }
        assert source_with_provenance.working_line_map == {
            1: 3,
            2: 4,
        }
