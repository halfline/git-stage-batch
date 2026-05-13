"""Tests for structural batch merge algorithm."""

import pytest

from git_stage_batch.batch.match import (
    UniqueLinePosition,
    _build_unique_position_map,
    match_lines,
)
from git_stage_batch.batch.merge import (
    RegionKind,
    _RealizedEntries,
    _build_baseline_correspondence,
    _build_realized_entries_for_discard,
    _check_structural_validity,
    _discard_batch_line_chunks,
    _merge_batch_line_chunks,
    _realized_entry_content_chunks,
    _try_apply_baseline_replacement_units,
    can_merge_batch_from_line_sequences,
    discard_batch_from_line_sequences_as_buffer,
    merge_batch_from_line_sequences_as_buffer,
    _satisfy_constraints,
)
from git_stage_batch.editor import EditorBuffer
from git_stage_batch.exceptions import MergeError
from git_stage_batch.batch.ownership import (
    BaselineReference,
    BatchOwnership,
    DeletionClaim,
    ReplacementUnit,
)
from git_stage_batch.utils.text import normalize_line_sequence_endings


class _IndexGuardedEditorBuffer(EditorBuffer):
    """EditorBuffer variant that rejects public line indexing in tests."""

    def __getitem__(self, index):
        raise AssertionError("public line indexing should not be used")


class _IndexGuardedRealizedEntries(_RealizedEntries):
    """Realized entries variant that rejects entry-view indexing in tests."""

    def __getitem__(self, index):
        raise AssertionError("entry indexing should not be used")


def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
    *,
    source_to_working_mapping=None,
) -> bytes:
    """Return merged bytes through the buffer-returning production API."""
    with (
        EditorBuffer.from_bytes(batch_source_content) as source_lines,
        EditorBuffer.from_bytes(working_content) as working_lines,
        merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            source_to_working_mapping=source_to_working_mapping,
        ) as buffer,
    ):
        return buffer.to_bytes()


def discard_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
    baseline_content: bytes,
) -> bytes:
    """Return discarded bytes through the buffer-returning production API."""
    with (
        EditorBuffer.from_bytes(batch_source_content) as source_lines,
        EditorBuffer.from_bytes(working_content) as working_lines,
        EditorBuffer.from_bytes(baseline_content) as baseline_lines,
        discard_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            baseline_lines,
        ) as buffer,
    ):
        return buffer.to_bytes()


class TestMatchLines:
    """Tests for line alignment using difflib.SequenceMatcher."""

    def test_unique_position_map_returns_structured_positions(self):
        """Unique line scanning returns positions without duplicate entries."""
        lines = [b"same\n", b"unique\n", b"same\n", b"other\n"]

        positions = _build_unique_position_map(lines, 0, len(lines))

        assert positions == {
            b"unique\n": UniqueLinePosition(index=1),
            b"other\n": UniqueLinePosition(index=3),
        }

    def test_line_mapping_uses_zero_filled_arrays(self):
        """Line mappings store one integer slot per line."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", b"line3\n"]

        mapping = match_lines(source, target)

        assert mapping.source_to_target.typecode in {"I", "Q"}
        assert mapping.target_to_source.typecode in {"I", "Q"}
        assert mapping.source_to_target.tolist() == [1, 0, 2]
        assert mapping.target_to_source.tolist() == [1, 3]
        assert mapping.get_target_line_from_source_line(2) is None

    def test_identical_files(self):
        """Test alignment of identical files."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", b"line2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # All lines should be present and map 1:1
        assert mapping.is_source_line_present(1)
        assert mapping.is_source_line_present(2)
        assert mapping.is_source_line_present(3)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 2
        assert mapping.get_target_line_from_source_line(3) == 3

    def test_accepts_non_list_sequences(self, line_sequence):
        """match_lines only requires sized indexable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        target = line_sequence([b"line1\n", b"extra\n", b"line2\n", b"line3\n"])

        mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_acquires_editor_buffer_lines(self):
        """EditorBuffer inputs are matched through scoped line acquisition."""
        with (
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nline2\nline3\n"
            ) as source,
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nextra\nline2\nline3\n"
            ) as target,
        ):
            mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_acquires_normalized_editor_buffer_lines(self):
        """Normalized EditorBuffer inputs forward scoped acquisition."""
        with (
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\r\nline2\nline3\n"
            ) as source_buffer,
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nextra\nline2\nline3\n"
            ) as target_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            target = normalize_line_sequence_endings(target_buffer)

            mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_working_tree_additions(self):
        """Test alignment when working tree has extra lines."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", "extra1\n", b"line2\n", "extra2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # Source lines map to target positions
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 5

        # Extra target lines map to None (not in source)
        assert mapping.get_source_line_from_target_line(2) is None
        assert mapping.get_source_line_from_target_line(4) is None

    def test_working_tree_deletions(self):
        """Test alignment when working tree is missing lines."""
        source = [b"line1\n", b"line2\n", b"line3\n", b"line4\n", b"line5\n"]
        target = [b"line1\n", b"line3\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Present lines map correctly
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(3) == 2
        assert mapping.get_target_line_from_source_line(5) == 3

        # Missing lines map to None
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(4) is None

    def test_replace_block_non_strict_no_match(self):
        """Test replace block where sub-matcher finds no matches."""
        source = [b"line1\n", "old2\n", "old3\n", b"line4\n"]
        target = [b"line1\n", "new2\n", "new3\n", b"line4\n"]

        mapping = match_lines(source, target)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(4) == 4

        # Replace block: sub-matcher sees these as completely different
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(3) is None

    def test_replace_block_non_strict_with_internal_match(self):
        """Test replace block where sub-matcher finds internal matches."""
        source = [b"line1\n", "A\n", "B\n", "C\n", b"line5\n"]
        target = [b"line1\n", "X\n", "B\n", "Y\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Equal blocks
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(5) == 5

        # Sub-matcher finds "B" matches within replace block
        assert mapping.get_target_line_from_source_line(3) == 3

        # A and C don't match
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(4) is None

    def test_replace_block_with_reordered_lines(self):
        """Test replace block with reordered lines shows sub-matcher behavior."""
        # Reordered replace block: A, B, C -> B, A, C.
        source = [b"line1\n", "A\n", "B\n", "C\n", b"line5\n"]
        target = [b"line1\n", "B\n", "A\n", "C\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Equal blocks (unchanged)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(5) == 5

        # Sub-matcher behavior with reordering:
        # SequenceMatcher may not perfectly handle all reorderings
        # It finds longest common subsequences, which in this case:
        # - A (source line 2) maps to A (target line 3)
        # - B gets treated as delete + reinsert (maps to None)
        # - C (source line 4) maps to C (target line 4)
        assert mapping.get_target_line_from_source_line(2) == 3  # A matches
        assert mapping.get_target_line_from_source_line(3) is None  # B seen as moved
        assert mapping.get_target_line_from_source_line(4) == 4  # C matches

        # This shows that sub-matching is better than pure positional,
        # but not perfect for all reorderings (trade-off for performance)

    def test_replace_block_strict(self):
        """Test alignment with replace block in strict mode."""
        source = [b"line1\n", "old2\n", b"line3\n"]
        target = [b"line1\n", "new2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(3) == 3

        # Replace block: source line maps to None in strict mode
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_source_line_from_target_line(2) is None


class TestMergeLineSequences:
    """Tests for merge helpers accepting non-list line sequences."""

    def test_constraint_helpers_accept_non_list_sequences(self, line_sequence):
        """Read-only merge helpers only require sized indexable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])
        mapping = match_lines(source, working)

        _check_structural_validity(
            mapping,
            {2},
            [],
            source,
            working,
        )
        entries = _satisfy_constraints(
            source,
            working,
            {2},
            [],
            source_to_working_mapping=mapping,
        )

        assert isinstance(entries, _RealizedEntries)
        assert b"".join(entry.content for entry in entries) == b"line1\nline2\nline3\n"

    def test_discard_entry_builder_accepts_non_list_sequences(self, line_sequence):
        """Discard entry construction only requires sized iterable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])
        mapping = match_lines(source, working)

        entries = _build_realized_entries_for_discard(source, working, mapping)

        assert isinstance(entries, _RealizedEntries)
        assert [entry.content for entry in entries] == [b"line1\n", b"line3\n"]
        assert [entry.source_line for entry in entries] == [1, 3]

    def test_realized_entry_content_chunks_avoids_entry_views(self):
        """Realized content streaming should not index compact entry storage."""
        entries = _IndexGuardedRealizedEntries()
        entries.append(b"line1\n")
        entries.append(b"line2\n")

        assert list(_realized_entry_content_chunks(entries)) == [
            b"line1\n",
            b"line2\n",
        ]

    def test_baseline_correspondence_accepts_non_list_sequences(self, line_sequence):
        """Baseline correspondence accepts sized sliceable line sequences."""
        baseline = line_sequence([b"line1\n", b"old\n", b"line3\n"])
        source = line_sequence([b"line1\n", b"new\n", b"line3\n"])

        correspondence = _build_baseline_correspondence(baseline, source)
        region = correspondence.get_region_for_source_line(2)

        assert region is not None
        assert region.kind == RegionKind.REPLACE_BY_HUNK
        assert tuple(region.baseline_lines) == (b"old\n",)
        assert not isinstance(region.baseline_lines, list)

    def test_can_merge_accepts_non_list_sequences(self, line_sequence):
        """Mergeability probes accept indexed line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])

        assert can_merge_batch_from_line_sequences(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
        ) is True

    def test_merge_from_line_sequences_can_return_buffer(self, line_sequence):
        """Merge can return a buffer without materializing through the bytes API."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\r\n", b"line3\r\n"])

        with merge_batch_from_line_sequences_as_buffer(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
        ) as result:
            assert result.to_bytes() == b"line1\r\nline2\r\nline3\r\n"

    def test_discard_from_line_sequences_can_return_buffer(self, line_sequence):
        """Discard can return a buffer without materializing through the bytes API."""
        baseline = line_sequence([b"line1\n", b"old\n", b"line3\n"])
        source = line_sequence([b"line1\n", b"new\n", b"line3\n"])
        working = line_sequence([b"line1\r\n", b"new\r\n", b"line3\r\n"])

        with discard_batch_from_line_sequences_as_buffer(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
            baseline,
        ) as result:
            assert result.to_bytes() == b"line1\r\nold\r\nline3\r\n"

    def test_merge_chunks_acquire_normalized_editor_buffer_lines(self):
        """Merge realization uses scoped normalized line acquisition."""
        with (
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nline2\nline3\n"
            ) as source_buffer,
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nline3\n"
            ) as working_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            working = normalize_line_sequence_endings(working_buffer)

            result = b"".join(
                _merge_batch_line_chunks(
                    source,
                    BatchOwnership.from_presence_lines(["2"], []),
                    working,
                )
            )

        assert result == b"line1\nline2\nline3\n"

    def test_discard_chunks_acquire_normalized_editor_buffer_lines(self):
        """Discard realization uses scoped normalized line acquisition."""
        with (
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nnew\nline3\n"
            ) as source_buffer,
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nnew\nline3\n"
            ) as working_buffer,
            _IndexGuardedEditorBuffer.from_bytes(
                b"line1\nold\nline3\n"
            ) as baseline_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            working = normalize_line_sequence_endings(working_buffer)
            baseline = normalize_line_sequence_endings(baseline_buffer)

            result = b"".join(
                _discard_batch_line_chunks(
                    source,
                    BatchOwnership.from_presence_lines(["2"], []),
                    working,
                    baseline,
                )
            )

        assert result == b"line1\nold\nline3\n"


class TestMergeBatch:
    """Tests for batch merge algorithm."""

    def test_merge_identical_files(self):
        """Test merge when files are identical (no-op)."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        result = merge_batch(source, BatchOwnership([], []), working)
        assert result == working

    def test_merge_add_missing_claimed_line(self):
        """Test merge that adds a missing claimed line."""
        source = b"line1\nline2\nline3\nline4\nline5\n"
        working = b"line1\nline3\nline5\n"  # Missing lines 2, 4
        claimed = ["2"]  # Claim line 2

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert line2 between line1 and line3
        assert result == b"line1\nline2\nline3\nline5\n"

    def test_merge_preserves_target_crlf_endings(self):
        """Merge should not turn a CRLF target into LF bytes."""
        source = b"line1\r\nline2\r\nline3\r\n"
        working = b"line1\r\nline3\r\n"
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        assert result == b"line1\r\nline2\r\nline3\r\n"

    def test_merge_preserves_working_tree_extras(self):
        """Test that merge preserves working tree extras."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nextra1\nline2\nextra2\nline3\n"
        claimed = ["2"]  # Claim line2

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should preserve extras
        assert result == working

    def test_baseline_referenced_presence_is_noop_when_already_present(self):
        """Baseline-coordinate insertion fallback should satisfy, not duplicate."""
        source = b"base\nfoo\nbar\n"
        working = b"base\nfoo\nbar\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [],
            baseline_references={
                2: BaselineReference(
                    after_line=1,
                    after_content=b"base",
                    before_line=2,
                    before_content=b"bar",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == working

    def test_baseline_referenced_presence_inserts_when_missing(self):
        """Baseline-coordinate insertion fallback still handles baseline targets."""
        source = b"base\nfoo\nbar\n"
        working = b"base\nbar\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [],
            baseline_references={
                2: BaselineReference(
                    after_line=1,
                    after_content=b"base",
                    before_line=2,
                    before_content=b"bar",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == source

    def test_baseline_referenced_noncontiguous_presence_is_noop_when_source_matches(self):
        """Already-satisfied additions may be interleaved with unclaimed source lines."""
        source = b"line1\nline2\nline3\nline4\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )

        result = merge_batch(source, ownership, source)

        assert result == source

    def test_baseline_referenced_noncontiguous_presence_inserts_subset_when_missing(self):
        """Baseline-coordinate insertion can stage selected additions without siblings."""
        source = b"line1\nline2\nline3\nline4\n"
        working = b"line1\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == b"line1\nline2\nline4\n"

    def test_baseline_referenced_fallback_yields_line_chunks(self):
        """Baseline-coordinate fallback returns line content chunks."""
        source_lines = [b"line1\n", b"line2\n", b"line3\n", b"line4\n"]
        working_lines = [b"line1\n"]
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )
        fallback_chunks = _try_apply_baseline_replacement_units(
            source_lines,
            working_lines,
            ownership,
            {2, 4},
            [],
        )

        assert fallback_chunks is not None
        assert list(fallback_chunks) == [b"line1\n", b"line2\n", b"line4\n"]

    def test_merge_with_deletion_suppresses_content(self):
        """Test that deletion constraints suppress matching content."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nline3\n"

        # Create deletion claim to suppress "unwanted"
        deletions = [DeletionClaim(anchor_line=None, content_lines=[b"unwanted\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the unwanted line
        assert result == b"line1\nline2\nline3\n"

    def test_merge_with_deletion_accepts_non_list_content_lines(self, line_sequence):
        """Deletion suppression only requires indexed content lines."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nline3\n"
        deletions = [
            DeletionClaim(
                anchor_line=None,
                content_lines=line_sequence([b"unwanted\n"]),
            ),
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        assert result == b"line1\nline2\nline3\n"

    def test_baseline_referenced_absence_suppresses_when_source_anchor_missing(self):
        """Absence-only fallback should use exact baseline coordinates."""
        source = b"line1\nnew context\nline3\n"
        working = b"line1\nold value\nline3\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                DeletionClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"line1\nline3\n"

    def test_baseline_referenced_absence_is_noop_when_already_absent(self):
        """Already-satisfied absence constraints should not block a round trip."""
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                DeletionClaim(
                    anchor_line=1,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        result = merge_batch(b"", ownership, b"")

        assert result == b""

    def test_baseline_referenced_replacement_is_noop_when_source_matches(self):
        """Applying replacement ownership back to its own source is a no-op."""
        source = b"A\nsame\n"
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            [
                DeletionClaim(
                    anchor_line=None,
                    content_lines=[b"same\n"],
                    baseline_reference=BaselineReference(
                        after_line=None,
                        before_line=2,
                        before_content=b"same",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                1: BaselineReference(
                    after_line=None,
                    before_line=2,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["1"],
                    deletion_indices=[0],
                )
            ],
        )

        result = merge_batch(source, ownership, source)

        assert result == source

    def test_baseline_referenced_independent_presence_and_absence(self):
        """Independent baseline-coordinate insertions and removals can compose."""
        source = b"x\nsame\nsame\nc\nsame\nc\n"
        working = b"same\na\nc\n"
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            [
                DeletionClaim(
                    anchor_line=5,
                    content_lines=[b"a\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"same",
                        before_line=3,
                        before_content=b"c",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                1: BaselineReference(
                    after_line=None,
                    before_line=1,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == b"x\nsame\nc\n"

    def test_baseline_referenced_absence_declines_when_content_changed(self):
        """Baseline-coordinate fallback should not remove changed target bytes."""
        source = b"line1\nnew context\nline3\n"
        working = b"line1\nother value\nline3\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                DeletionClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_merge_with_deletion_after_line(self):
        """Test deletion constraint removes content at specific position."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nunwanted\nline3\n"

        # Create deletion claim to suppress "unwanted" after line 2
        deletions = [DeletionClaim(anchor_line=2, content_lines=[b"unwanted\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the unwanted line
        assert result == b"line1\nline2\nline3\n"

    def test_merge_deletion_no_match_preserves_content(self):
        """Test that deletion constraint with no match preserves content."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\ndifferent\nline3\n"

        # Create deletion claim for content that doesn't exist
        deletions = [DeletionClaim(anchor_line=2, content_lines=[b"nonexistent\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should preserve all content (no match to suppress)
        assert result == b"line1\nline2\ndifferent\nline3\n"

    def test_merge_deletion_position_aware_not_global(self):
        """Test that deletion constraint is position-aware, not global removal.

        This validates that deletions suppress content at their anchored position,
        not globally throughout the file.
        """
        source = b"line1\nline2\nline3\n"
        working = b"duplicate\nline1\nduplicate\nline2\nline3\n"

        # Create deletion claim anchored at start-of-file
        deletions = [DeletionClaim(anchor_line=None, content_lines=[b"duplicate\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should only suppress first "duplicate" (at anchor position)
        # Second "duplicate" should remain (different structural position)
        lines = result.splitlines(keepends=True)
        duplicate_count = sum(1 for line in lines if line == b"duplicate\n")
        assert duplicate_count == 1, "Should only remove duplicate at anchored position"
        assert b"line1\n" in lines
        assert b"duplicate\n" in lines  # Second occurrence remains

    def test_merge_deletion_multiline_sequence(self):
        """Test deletion constraint with multi-line sequence."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nblock_start\nblock_end\nline2\nline3\n"

        # Create deletion claim for multi-line sequence
        deletions = [DeletionClaim(anchor_line=1, content_lines=[b"block_start\n", b"block_end\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the entire sequence
        assert result == b"line1\nline2\nline3\n"

    def test_merge_interleaved_even_odd_batches(self):
        """Test merging interleaved batches (pathological case from plan)."""
        # File with 10 lines
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"

        # Working tree with all lines removed
        working = b""

        # Batch 1: even lines (2, 4, 6, 8, 10)
        even_claimed = ["2", "4", "6", "8", "10"]

        # Apply even lines first
        result1 = merge_batch(source, BatchOwnership.from_presence_lines(even_claimed, []), working)
        assert result1 == b"line2\nline4\nline6\nline8\nline10\n"

        # Now apply odd lines on top of even
        odd_claimed = ["1", "3", "5", "7", "9"]
        result2 = merge_batch(source, BatchOwnership.from_presence_lines(odd_claimed, []), result1)

        # Should interleave correctly
        expected = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        assert result2 == expected

    def test_merge_interleaved_odd_then_even(self):
        """Test merging interleaved batches in reverse order."""
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        working = b""

        # Apply odd first
        odd_claimed = ["1", "3", "5", "7", "9"]
        result1 = merge_batch(source, BatchOwnership.from_presence_lines(odd_claimed, []), working)
        assert result1 == b"line1\nline3\nline5\nline7\nline9\n"

        # Then apply even
        even_claimed = ["2", "4", "6", "8", "10"]
        result2 = merge_batch(source, BatchOwnership.from_presence_lines(even_claimed, []), result1)

        # Should produce same result as even-then-odd
        expected = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        assert result2 == expected

    def test_merge_with_duplicate_lines_uses_alignment(self):
        """Test that merge uses structural alignment, not text matching."""
        # Source has duplicate "dup" lines
        source = b"line1\ndup\nline3\ndup\nline5\n"

        # Working tree is missing first dup
        working = b"line1\nline3\ndup\nline5\n"

        # Claim line 2 (first "dup")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first dup based on alignment, not text search
        # Result should have both dups in correct positions
        assert result == b"line1\ndup\nline3\ndup\nline5\n"

    def test_merge_with_low_entropy_duplicates_blank_lines(self):
        """Test alignment with duplicate blank lines (low-entropy content)."""
        # Source has multiple blank lines in specific positions
        source = b"line1\n\nline3\n\nline5\n"

        # Working tree missing first blank line
        working = b"line1\nline3\n\nline5\n"

        # Claim line 2 (first blank line)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first blank line at correct position via alignment
        assert result == b"line1\n\nline3\n\nline5\n"

    def test_merge_with_low_entropy_duplicates_braces(self):
        """Test alignment with duplicate braces (common in code)."""
        # Source has multiple closing braces
        source = b"func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

        # Working tree missing first closing brace
        working = b"func1() {\nfunc2() {\n}\nfunc3() {\n}\n"

        # Claim line 2 (first "}")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first brace at correct position via alignment
        assert result == b"func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

    def test_merge_preserves_working_tree_reordering(self):
        """Test that working tree extras are preserved even when reordered."""
        source = b"A\nB\nC\n"
        working = b"A\nX\nB\nY\nC\nZ\n"

        # Claim all source lines (no-op for content, but tests preservation)
        claimed = ["1", "2", "3"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Extras should remain in their positions
        assert result == b"A\nX\nB\nY\nC\nZ\n"

    def test_merge_with_reordered_source_lines_in_working_tree(self):
        """Test merge when source lines are reordered in working tree."""
        # Batch source has A, B, C in order
        source = b"line1\nA\nB\nC\nline5\n"

        # Working tree has same lines but B and A swapped
        working = b"line1\nB\nA\nC\nline5\n"

        # Claim line 2 (A in batch source)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # A should already be present at line 3, so shouldn't duplicate
        # (semantic matching finds it despite different position)
        assert result.count(b"A\n") == 1
        assert b"A\n" in result

    def test_merge_claimed_range(self):
        """Test merge with claimed range."""
        source = b"line1\nline2\nline3\nline4\nline5\n"
        working = b"line1\nline5\n"

        # Claim lines 2-4
        claimed = ["2-4"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        assert result == b"line1\nline2\nline3\nline4\nline5\n"

    def test_merge_multiple_deletions(self):
        """Test merge with multiple deletion constraints at different positions."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted1\nline1\nline2\nunwanted2\nline3\n"

        deletions = [
            DeletionClaim(anchor_line=None, content_lines=[b"unwanted1\n"]),
            DeletionClaim(anchor_line=2, content_lines=[b"unwanted2\n"])
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Both deletion constraints should be enforced
        assert result == b"line1\nline2\nline3\n"

    def test_merge_multiple_deletions_same_content(self):
        """Test that multiple deletion constraints for same content suppress all occurrences."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nunwanted\nline3\n"

        # Two deletion constraints for same content (e.g., from incremental batching)
        deletions = [
            DeletionClaim(anchor_line=None, content_lines=[b"unwanted\n"]),
            DeletionClaim(anchor_line=2, content_lines=[b"unwanted\n"])
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Both occurrences should be suppressed
        assert result == b"line1\nline2\nline3\n"

    def test_merge_preserves_existing_crlf_line_endings(self):
        """Test that merge keeps the target file's line endings."""
        source = b"line1\nline2\nline3\n"  # Already normalized
        working = b"line1\r\nline2\r\nline3\r\n"  # Windows line endings

        result = merge_batch(source, BatchOwnership([], []), working)

        assert result == working

    def test_merge_large_file_performance(self):
        """Test merge performance with large files (10k+ lines)."""
        # Create large source file
        source_lines = [f"line{i}\n".encode() for i in range(1, 10001)]
        source = b"".join(source_lines)

        # Working tree with 1000 lines inserted at top
        extra_lines = [f"extra{i}\n".encode() for i in range(1, 1001)]
        working = b"".join(extra_lines + source_lines)

        # Claim every 100th line
        claimed = [str(i) for i in range(100, 10001, 100)]

        # This should complete quickly (difflib.SequenceMatcher is fast in practice)
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Verify result has both extras and source
        assert result.startswith(b"extra1\n")
        assert b"line1\n" in result
        assert b"line10000\n" in result


class TestMergeErrors:
    """Tests for merge error conditions."""

    def test_merge_error_claimed_line_out_of_range(self):
        """Test error when claimed line is out of range."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        claimed = ["100"]  # Out of range

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_error_deletion_anchor_out_of_range(self):
        """Test error when deletion anchor is out of range."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        deletions = [DeletionClaim(anchor_line=100, content_lines=[b"unwanted\n"])]

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership([], deletions), working)

    def test_merge_error_claimed_line_no_context(self):
        """Test error when claimed line has no surrounding context."""
        # Source with lines 1-10
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"

        # Working tree completely rewritten (no alignment possible)
        working = b"\n".join([f"different{i}".encode() for i in range(1, 11)]) + b"\n"

        # Claim line 5 (middle line with no aligned neighbors)
        claimed = ["5"]

        # Should fail because cannot reliably place line 5
        with pytest.raises(MergeError, match="Cannot reliably place"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_succeeds_with_minimal_context(self):
        """Test that merge succeeds when there's minimal but sufficient context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing line 3 but has neighbors
        working = b"line1\nline2\nline4\nline5\n"

        # Claim missing line 3
        claimed = ["3"]

        # Should succeed because lines 2 and 4 provide context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert result == b"line1\nline2\nline3\nline4\nline5\n"

    def test_merge_succeeds_with_only_trailing_context(self):
        """Test merge with missing line that only has trailing (after) context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing lines 1-2 but has line3 onwards
        working = b"different1\ndifferent2\nline3\nline4\nline5\n"

        # Claim line 2 - no leading context but has trailing (line3)
        claimed = ["2"]

        # Should succeed - line3 provides trailing context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line2" in result

    def test_merge_succeeds_with_only_leading_context(self):
        """Test merge with missing line that only has leading (before) context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree has line1-3 but then different content
        working = b"line1\nline2\nline3\ndifferent4\ndifferent5\n"

        # Claim line 4 - has leading context (line3) but no trailing
        claimed = ["4"]

        # Should succeed - line3 provides leading context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line4" in result

    def test_merge_requires_context_even_at_edges(self):
        """Test that edge lines require context too (no special case)."""
        source = b"line1\nline2\nline3\n"

        # Working tree has middle line only
        working = b"different1\nline2\ndifferent3\n"

        # Claim first line - has context (line2 is aligned)
        claimed = ["1"]

        # Should succeed - line2 provides context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line1" in result

    def test_merge_edge_lines_fail_without_neighbors(self):
        """Test that edge lines fail when completely isolated."""
        source = b"line1\nline2\nline3\n"

        # Working tree completely different
        working = b"different1\ndifferent2\ndifferent3\n"

        # Claim first line with no aligned neighbors
        claimed = ["1"]

        # Should fail - file completely rewritten
        with pytest.raises(MergeError, match="file completely rewritten"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_error_batch_created_from_later_file_state(self):
        """Test error when batch was created from later file state with extra context.

        This simulates the problem encountered during pristine history reconstruction:
        - A batch is created from final file state (many features added)
        - Batch is applied to earlier file state (features don't exist yet)
        - The batch contains changes that depend on context not yet in the file
        - Should raise MergeError rather than silently corrupting the file

        Scenario:
        - Later file state has parser_status section followed by parser_include section
        - Earlier file state only has parser_status section
        - Batch adds --porcelain argument to parser_status
        - Batch also has a deletion that removes the old set_defaults line
        - The deletion is adjacent to context that doesn't exist in earlier state
        """
        # Earlier file state: basic status command (6 lines)
        working_early = b"line1\nline2\nline3\nline4\nline5\nline6\n"

        # Later file state: status command + extra features (12 lines)
        # Lines 1-6: same as before
        # Lines 7-12: new parser_include section added AFTER status in later history
        source_later = b"line1\nline2\nline3\nmodified4\nline5\nline6\nline7\nline8\nline9\nline10\nline11\nline12\n"

        # Batch claims line 4 (modification to status section)
        # In source_later, line 4 has "modified4" instead of "line4"
        # The batch wants to merge this change back
        claimed = ["4"]  # Modified line in the middle of status section

        # But the batch also includes a DELETION that depends on context from lines 7-12
        # Specifically, it wants to delete line 6 with anchor near line 7
        deletions = [DeletionClaim(anchor_line=7, content_lines=[b"line6\n"])]

        # Trying to merge should detect that:
        # 1. The deletion anchor (line 7 in source_later) doesn't exist in working_early
        # 2. Working tree only has 6 lines, source has 12
        # 3. The structural mismatch is too large to safely merge

        # This correctly raises MergeError because deletion anchor doesn't exist
        with pytest.raises(MergeError, match="anchor not present"):
            merge_batch(source_later, BatchOwnership.from_presence_lines(claimed, deletions), working_early)

    def test_merge_produces_corruption_with_mismatched_context(self):
        """Reproduce corruption when merging batch from later state to earlier state.

        Real scenario from pristine history reconstruction attempt:

        Working tree (earlier state - commit e05ce02c):
            Line 1: parser_status = subparsers.add_parser(...)
            Line 2:     "status",
            Line 3: )
            Line 4: parser_status.set_defaults(func=lambda _: ...)
            Line 5:
            Line 6: # Parse arguments

        Batch source (later state - commit a6af5fa6):
            Line 1: parser_status = subparsers.add_parser(...)
            Line 2:     "status",
            Line 3: )
            Line 4: parser_status.set_defaults(func=lambda _: ...)  [TO DELETE]
            Line 5: parser_status.add_argument("--porcelain"...)    [TO ADD]
            Line 6: parser_status.set_defaults(func=lambda args: ...)  [TO ADD]
            Line 7:
            Line 8: # include - Stage the selected hunk
            Line 9: parser_include = subparsers.add_parser(...)

        Batch ownership:
            - Claimed: line 5-6 (new --porcelain argument + new set_defaults)
            - Deletion: line 4 (old set_defaults) anchored after line 3

        When applied to working tree WITHOUT matching context (no parser_include):
            Result should be: lines 1-3, then new 5-6, then line 6
            Corruption: BOTH old line 4 AND new line 6 present (duplicate set_defaults)
        """
        # Working tree: only 6 lines, ends after old set_defaults
        working = b"""parser_status = subparsers.add_parser(
    "status",
)
parser_status.set_defaults(func=lambda _: commands.command_status())

# Parse arguments
"""

        # Batch source: 9+ lines, has parser_include section after
        source = b"""parser_status = subparsers.add_parser(
    "status",
)
parser_status.set_defaults(func=lambda _: commands.command_status())
parser_status.add_argument("--porcelain", action="store_true")
parser_status.set_defaults(func=lambda args: commands.command_status(porcelain=args.porcelain))

# include - Stage the selected hunk
parser_include = subparsers.add_parser(
"""

        # Batch wants to:
        # 1. Delete line 4 (old set_defaults) with anchor after line 3
        # 2. Add lines 5-6 (--porcelain arg + new set_defaults)
        deletions = [DeletionClaim(
            anchor_line=3,
            content_lines=[b"parser_status.set_defaults(func=lambda _: commands.command_status())\n"]
        )]
        claimed = ["5", "6"]  # New argument and new set_defaults

        # Apply the merge
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, deletions), working)

        # Check that old and new set_defaults are not both present.
        result_str = result.decode()
        old_setdefaults = "lambda _: commands.command_status()"
        new_setdefaults = "lambda args: commands.command_status(porcelain=args.porcelain)"

        has_old = old_setdefaults in result_str
        has_new = new_setdefaults in result_str

        print("\n=== MERGE RESULT ===")
        print(result_str)
        print("=== END RESULT ===")
        print(f"Has old set_defaults: {has_old}")
        print(f"Has new set_defaults: {has_new}")

        if has_old and has_new:
            pytest.fail(
                "both old and new set_defaults present. "
                "The deletion didn't work correctly when context doesn't match."
            )

    def test_batch_with_changes_to_nonexistent_sections(self):
        """Test applying batch that modifies sections not present in working tree.

        Real corruption scenario from classification:

        Batch contains (from working tree diff):
            Section A changes (parser_show): Remove --line, --file args
            Section B changes (parser_status): Add --porcelain arg  <-- intended
            Section C changes (parser_include): Simplify --file arg

        Working tree (earlier commit e05ce02c):
            Only has basic Section B (parser_status)
            Does not have Sections A or C yet.

        When batch applied:
            - Section A deletions fail to match (parser_show doesn't exist)
            - Section B changes apply (parser_status exists)
            - Section C deletions fail to match (parser_include doesn't exist)
            - Result: partial application with context confusion

        The batch was created correctly for working tree state,
        but contains changes across multiple code sections.
        When those sections don't all exist in target, merge fails.
        """
        # Working tree: only section B exists
        working = b"""# Section B
line1_b
line2_b
line3_b
"""

        # Batch source: all sections exist
        source = b"""# Section A
line1_a
line2_a
line3_a

# Section B
line1_b
line2_b_MODIFIED
line3_b

# Section C
line1_c
line2_c
line3_c
"""

        # Batch modifies line in Section B (which exists in working)
        # But also has deletions from Section A (which doesn't exist)
        claimed = ["7"]  # line2_b_MODIFIED in source
        deletions = [
            # Try to delete from Section A (anchor at line 2)
            DeletionClaim(anchor_line=2, content_lines=[b"line1_a\n"])
        ]

        # This should fail because Section A doesn't exist in working tree
        # The anchor line 2 doesn't map correctly
        with pytest.raises(MergeError):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, deletions), working)


class TestDiscardBatch:
    """Tests for discard_batch function (inverse of merge_batch)."""

    def test_discard_simple_claimed_line(self):
        """Test discarding a single claimed line restores baseline."""
        baseline = b"original\n"
        batch_source = b"modified\n"
        working = b"modified\n"

        # Claim the modified line
        ownership = BatchOwnership.from_presence_lines(["1"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore baseline
        assert result == b"original\n"

    def test_discard_preserves_non_batch_content(self):
        """Test that non-batch content is preserved."""
        baseline = b"line1\nline2\nline3\n"
        batch_source = b"line1\nmodified2\nline3\n"
        working = b"line1\nmodified2\nextra\nline3\n"

        # Claim only line 2 (modified2)
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore line2 from baseline, keep extra line
        assert result == b"line1\nline2\nextra\nline3\n"

    def test_discard_after_divergence(self):
        """Test discarding after working tree diverged from batch source."""
        baseline = b"A\nB\nC\nD\nE\n"
        batch_source = b"A\nB_modified\nC\nD\nE\n"
        # Working tree added lines at top
        working = b"X\nY\nZ\nA\nB_modified\nC\nD\nE\n"

        # Claim the modified B
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline, keep X, Y, Z
        assert result == b"X\nY\nZ\nA\nB\nC\nD\nE\n"

    def test_discard_with_insertion(self):
        """Test discarding insertion removes it."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninserted\nline2\n"  # Batch added "inserted"
        working = b"line1\ninserted\nline2\n"

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["2"], [])  # Line 2 of batch_source is "inserted"

        result = discard_batch(batch_source, ownership, working, baseline)

        # Insertion should be removed (maps to "insert" region with no baseline)
        assert result == b"line1\nline2\n"

    def test_discard_with_insertion_at_start(self):
        """Test discarding insertion at start of file."""
        baseline = b"line1\nline2\n"
        batch_source = b"inserted\nline1\nline2\n"  # Batch added "inserted" at start
        working = b"inserted\nline1\nline2\n"

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["1"], [])  # Line 1 of batch_source is "inserted"

        result = discard_batch(batch_source, ownership, working, baseline)

        # Start insertion should be removed
        assert result == b"line1\nline2\n"

    def test_discard_combined_claimed_and_insertion(self):
        """Test discarding both claimed lines and insertions."""
        baseline = b"A\nB\nC\n"
        batch_source = b"A\nB_modified\ninserted\nC\n"  # Modified B and added "inserted"
        working = b"A\nB_modified\ninserted\nC\n"

        # Claim both modified B and the insertion
        ownership = BatchOwnership.from_presence_lines(["2", "3"], [])  # Lines 2 and 3 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline (replace region) and remove insertion (insert region)
        assert result == b"A\nB\nC\n"

    def test_discard_multiple_insertions_same_position(self):
        """Test discarding multiple insertions at same position."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninsert1\ninsert2\nline2\n"  # Batch added two lines
        working = b"line1\ninsert1\ninsert2\nline2\n"

        # Claim both inserted lines
        ownership = BatchOwnership.from_presence_lines(["2", "3"], [])  # Lines 2 and 3 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Both insertions removed (both are insert regions with no baseline)
        assert result == b"line1\nline2\n"

    def test_discard_interleaved_batch_restores_baseline(self):
        """Test discarding interleaved batch (related to even/odd pathological case)."""
        baseline = b"1\n2\n3\n4\n5\n"
        batch_source = b"1\n2_mod\n3\n4_mod\n5\n"
        working = b"1\n2_mod\n3\n4_mod\n5\n"

        # Claim even lines (2, 4)
        ownership = BatchOwnership.from_presence_lines(["2", "4"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore even lines from baseline
        assert result == b"1\n2\n3\n4\n5\n"

    def test_discard_insertion_not_present_does_nothing(self):
        """Test that discarding insertion when not in working tree does nothing."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninserted\nline2\n"  # Batch added "inserted"
        working = b"line1\nline2\n"  # But working tree doesn't have it

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["2"], [])  # Line 2 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged (insertion not present, nothing to discard)
        assert result == b"line1\nline2\n"

    def test_discard_claimed_line_not_present_does_nothing(self):
        """Test that discarding missing claimed line doesn't affect working tree."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\nmodified2\n"
        working = b"line1\nline2\n"  # Already at baseline

        # Claim line 2, but it's not present in working tree
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged
        assert result == b"line1\nline2\n"
