"""Tests for structural batch merge algorithm."""

import pytest

from git_stage_batch.batch.match import match_lines
from git_stage_batch.batch.merge import merge_batch, discard_batch
from git_stage_batch.exceptions import MergeError
from git_stage_batch.utils.git import create_git_blob
from git_stage_batch.batch.ownership import BatchOwnership


class TestMatchLines:
    """Tests for line alignment using difflib.SequenceMatcher."""

    def test_identical_files(self):
        """Test alignment of identical files."""
        source = ["line1\n", "line2\n", "line3\n"]
        target = ["line1\n", "line2\n", "line3\n"]

        mapping = match_lines(source, target)

        # All lines should be present and map 1:1
        assert mapping.is_source_line_present(1)
        assert mapping.is_source_line_present(2)
        assert mapping.is_source_line_present(3)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 2
        assert mapping.get_target_line_from_source_line(3) == 3

    def test_working_tree_additions(self):
        """Test alignment when working tree has extra lines."""
        source = ["line1\n", "line2\n", "line3\n"]
        target = ["line1\n", "extra1\n", "line2\n", "extra2\n", "line3\n"]

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
        source = ["line1\n", "line2\n", "line3\n", "line4\n", "line5\n"]
        target = ["line1\n", "line3\n", "line5\n"]

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
        source = ["line1\n", "old2\n", "old3\n", "line4\n"]
        target = ["line1\n", "new2\n", "new3\n", "line4\n"]

        mapping = match_lines(source, target, strict=False)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(4) == 4

        # Replace block: sub-matcher sees these as completely different
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(3) is None

    def test_replace_block_non_strict_with_internal_match(self):
        """Test replace block where sub-matcher finds internal matches."""
        source = ["line1\n", "A\n", "B\n", "C\n", "line5\n"]
        target = ["line1\n", "X\n", "B\n", "Y\n", "line5\n"]

        mapping = match_lines(source, target, strict=False)

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
        # This is the critical case from feedback: A, B, C → B, A, C
        source = ["line1\n", "A\n", "B\n", "C\n", "line5\n"]
        target = ["line1\n", "B\n", "A\n", "C\n", "line5\n"]

        mapping = match_lines(source, target, strict=False)

        # Equal blocks (unchanged)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(5) == 5

        # Sub-matcher behavior with reordering:
        # SequenceMatcher may not perfectly handle all reorderings
        # It finds longest common subsequences, which in this case:
        # - A (source line 2) → A (target line 3) ✓
        # - B gets treated as delete + reinsert (maps to None)
        # - C (source line 4) → C (target line 4) ✓
        assert mapping.get_target_line_from_source_line(2) == 3  # A matches
        assert mapping.get_target_line_from_source_line(3) is None  # B seen as moved
        assert mapping.get_target_line_from_source_line(4) == 4  # C matches

        # This shows that sub-matching is better than pure positional,
        # but not perfect for all reorderings (trade-off for performance)

    def test_replace_block_strict(self):
        """Test alignment with replace block in strict mode."""
        source = ["line1\n", "old2\n", "line3\n"]
        target = ["line1\n", "new2\n", "line3\n"]

        mapping = match_lines(source, target, strict=True)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(3) == 3

        # Replace block: source line maps to None in strict mode
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_source_line_from_target_line(2) is None


class TestMergeBatch:
    """Tests for batch merge algorithm."""

    def test_merge_identical_files(self):
        """Test merge when files are identical (no-op)."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        result = merge_batch(source, BatchOwnership([], []), working)
        assert result == working

    def test_merge_add_missing_claimed_line(self):
        """Test merge that adds a missing claimed line."""
        source = "line1\nline2\nline3\nline4\nline5\n"
        working = "line1\nline3\nline5\n"  # Missing lines 2, 4
        claimed = ["2"]  # Claim line 2

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Should insert line2 between line1 and line3
        assert result == "line1\nline2\nline3\nline5\n"

    def test_merge_preserves_working_tree_extras(self):
        """Test that merge preserves working tree extras."""
        source = "line1\nline2\nline3\n"
        working = "line1\nextra1\nline2\nextra2\nline3\n"
        claimed = ["2"]  # Claim line2

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Should preserve extras
        assert result == working

    def test_merge_with_insertion_at_start(self):
        """Test merge with insertion at start of file."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        # Create blob for insertion
        blob_sha = create_git_blob([b"inserted\n"])
        insertions = [{"after_source_line": None, "blob": blob_sha}]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        assert result == "inserted\nline1\nline2\nline3\n"

    def test_merge_with_insertion_after_line(self):
        """Test merge with insertion after specific line."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        # Create blob for insertion after line 2
        blob_sha = create_git_blob([b"inserted\n"])
        insertions = [{"after_source_line": 2, "blob": blob_sha}]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        assert result == "line1\nline2\ninserted\nline3\n"

    def test_merge_insertion_already_present(self):
        """Test that merge skips insertion if already present."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nalready_here\nline3\n"

        # Create blob with content that's already there
        blob_sha = create_git_blob([b"already_here\n"])
        insertions = [{"after_source_line": 2, "blob": blob_sha}]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        # Should not duplicate
        assert result == "line1\nline2\nalready_here\nline3\n"

    def test_merge_insertion_present_with_trailing_whitespace_diff(self):
        """Test that insertion check handles trailing whitespace differences."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nalready_here  \t\nline3\n"  # Trailing whitespace

        # Create blob without trailing whitespace
        blob_sha = create_git_blob([b"already_here\n"])
        insertions = [{"after_source_line": 2, "blob": blob_sha}]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        # Should recognize as present despite trailing whitespace (rstrip check)
        assert result == "line1\nline2\nalready_here  \t\nline3\n"

    def test_merge_insertion_not_present_with_leading_whitespace_diff(self):
        """Test that insertion check preserves leading whitespace (indentation)."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\n    indented\nline3\n"  # Leading spaces

        # Create blob without leading whitespace
        blob_sha = create_git_blob([b"indented\n"])
        insertions = [{"after_source_line": 2, "blob": blob_sha}]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        # Should NOT recognize as present (different indentation = different line)
        # So insertion should be added
        assert result == "line1\nline2\nindented\n    indented\nline3\n"

    def test_merge_interleaved_even_odd_batches(self):
        """Test merging interleaved batches (pathological case from plan)."""
        # File with 10 lines
        source = "\n".join([f"line{i}" for i in range(1, 11)]) + "\n"

        # Working tree with all lines removed
        working = ""

        # Batch 1: even lines (2, 4, 6, 8, 10)
        even_claimed = ["2", "4", "6", "8", "10"]

        # Apply even lines first
        result1 = merge_batch(source, BatchOwnership(even_claimed, []), working)
        assert result1 == "line2\nline4\nline6\nline8\nline10\n"

        # Now apply odd lines on top of even
        odd_claimed = ["1", "3", "5", "7", "9"]
        result2 = merge_batch(source, BatchOwnership(odd_claimed, []), result1)

        # Should interleave correctly
        expected = "\n".join([f"line{i}" for i in range(1, 11)]) + "\n"
        assert result2 == expected

    def test_merge_interleaved_odd_then_even(self):
        """Test merging interleaved batches in reverse order."""
        source = "\n".join([f"line{i}" for i in range(1, 11)]) + "\n"
        working = ""

        # Apply odd first
        odd_claimed = ["1", "3", "5", "7", "9"]
        result1 = merge_batch(source, BatchOwnership(odd_claimed, []), working)
        assert result1 == "line1\nline3\nline5\nline7\nline9\n"

        # Then apply even
        even_claimed = ["2", "4", "6", "8", "10"]
        result2 = merge_batch(source, BatchOwnership(even_claimed, []), result1)

        # Should produce same result as even-then-odd
        expected = "\n".join([f"line{i}" for i in range(1, 11)]) + "\n"
        assert result2 == expected

    def test_merge_with_duplicate_lines_uses_alignment(self):
        """Test that merge uses structural alignment, not text matching."""
        # Source has duplicate "dup" lines
        source = "line1\ndup\nline3\ndup\nline5\n"

        # Working tree is missing first dup
        working = "line1\nline3\ndup\nline5\n"

        # Claim line 2 (first "dup")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Should insert first dup based on alignment, not text search
        # Result should have both dups in correct positions
        assert result == "line1\ndup\nline3\ndup\nline5\n"

    def test_merge_with_low_entropy_duplicates_blank_lines(self):
        """Test alignment with duplicate blank lines (low-entropy content)."""
        # Source has multiple blank lines in specific positions
        source = "line1\n\nline3\n\nline5\n"

        # Working tree missing first blank line
        working = "line1\nline3\n\nline5\n"

        # Claim line 2 (first blank line)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Should insert first blank line at correct position via alignment
        assert result == "line1\n\nline3\n\nline5\n"

    def test_merge_with_low_entropy_duplicates_braces(self):
        """Test alignment with duplicate braces (common in code)."""
        # Source has multiple closing braces
        source = "func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

        # Working tree missing first closing brace
        working = "func1() {\nfunc2() {\n}\nfunc3() {\n}\n"

        # Claim line 2 (first "}")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Should insert first brace at correct position via alignment
        assert result == "func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

    def test_merge_preserves_working_tree_reordering(self):
        """Test that working tree extras are preserved even when reordered."""
        source = "A\nB\nC\n"
        working = "A\nX\nB\nY\nC\nZ\n"

        # Claim all source lines (no-op for content, but tests preservation)
        claimed = ["1", "2", "3"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Extras should remain in their positions
        assert result == "A\nX\nB\nY\nC\nZ\n"

    def test_merge_with_reordered_source_lines_in_working_tree(self):
        """Test merge when source lines are reordered in working tree."""
        # Batch source has A, B, C in order
        source = "line1\nA\nB\nC\nline5\n"

        # Working tree has same lines but B and A swapped
        working = "line1\nB\nA\nC\nline5\n"

        # Claim line 2 (A in batch source)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # A should already be present at line 3, so shouldn't duplicate
        # (semantic matching finds it despite different position)
        assert result.count("A\n") == 1
        assert "A\n" in result

    def test_merge_claimed_range(self):
        """Test merge with claimed range."""
        source = "line1\nline2\nline3\nline4\nline5\n"
        working = "line1\nline5\n"

        # Claim lines 2-4
        claimed = ["2-4"]

        result = merge_batch(source, BatchOwnership(claimed, []), working)

        assert result == "line1\nline2\nline3\nline4\nline5\n"

    def test_merge_multiple_insertions(self):
        """Test merge with multiple insertions."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        blob1 = create_git_blob([b"insert1\n"])
        blob2 = create_git_blob([b"insert2\n"])
        insertions = [
            {"after_source_line": 1, "blob": blob1},
            {"after_source_line": 3, "blob": blob2}
        ]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        assert result == "line1\ninsert1\nline2\nline3\ninsert2\n"

    def test_merge_multiple_insertions_same_position(self):
        """Test that multiple insertions at same position accumulate."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        blob1 = create_git_blob([b"insert1\n"])
        blob2 = create_git_blob([b"insert2\n"])
        # Both insertions after line 1
        insertions = [
            {"after_source_line": 1, "blob": blob1},
            {"after_source_line": 1, "blob": blob2}
        ]

        result = merge_batch(source, BatchOwnership([], insertions), working)

        # Both should be inserted (accumulated, not last-one-wins)
        assert result == "line1\ninsert1\ninsert2\nline2\nline3\n"

    def test_merge_normalizes_line_endings(self):
        """Test that merge normalizes line endings."""
        source = "line1\nline2\nline3\n"  # Already normalized
        working = "line1\r\nline2\r\nline3\r\n"  # Windows line endings

        result = merge_batch(source, BatchOwnership([], []), working)

        # Result should use \n (normalized)
        assert result == "line1\nline2\nline3\n"

    def test_merge_large_file_performance(self):
        """Test merge performance with large files (10k+ lines)."""
        # Create large source file
        source_lines = [f"line{i}\n" for i in range(1, 10001)]
        source = "".join(source_lines)

        # Working tree with 1000 lines inserted at top
        extra_lines = [f"extra{i}\n" for i in range(1, 1001)]
        working = "".join(extra_lines + source_lines)

        # Claim every 100th line
        claimed = [str(i) for i in range(100, 10001, 100)]

        # This should complete quickly (difflib.SequenceMatcher is fast in practice)
        result = merge_batch(source, BatchOwnership(claimed, []), working)

        # Verify result has both extras and source
        assert result.startswith("extra1\n")
        assert "line1\n" in result
        assert "line10000\n" in result


class TestMergeErrors:
    """Tests for merge error conditions."""

    def test_merge_error_claimed_line_out_of_range(self):
        """Test error when claimed line is out of range."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        claimed = ["100"]  # Out of range

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership(claimed, []), working)

    def test_merge_error_insertion_after_invalid_line(self):
        """Test error when insertion anchor is out of range."""
        source = "line1\nline2\nline3\n"
        working = "line1\nline2\nline3\n"

        blob = create_git_blob([b"insert\n"])
        insertions = [{"after_source_line": 100, "blob": blob}]

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership([], insertions), working)

    def test_merge_error_claimed_line_no_context(self):
        """Test error when claimed line has no surrounding context."""
        # Source with lines 1-10
        source = "\n".join([f"line{i}" for i in range(1, 11)]) + "\n"

        # Working tree completely rewritten (no alignment possible)
        working = "\n".join([f"different{i}" for i in range(1, 11)]) + "\n"

        # Claim line 5 (middle line with no aligned neighbors)
        claimed = ["5"]

        # Should fail because cannot reliably place line 5
        with pytest.raises(MergeError, match="Cannot reliably place"):
            merge_batch(source, BatchOwnership(claimed, []), working)

    def test_merge_succeeds_with_minimal_context(self):
        """Test that merge succeeds when there's minimal but sufficient context."""
        source = "line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing line 3 but has neighbors
        working = "line1\nline2\nline4\nline5\n"

        # Claim missing line 3
        claimed = ["3"]

        # Should succeed because lines 2 and 4 provide context
        result = merge_batch(source, BatchOwnership(claimed, []), working)
        assert result == "line1\nline2\nline3\nline4\nline5\n"

    def test_merge_succeeds_with_only_trailing_context(self):
        """Test merge with missing line that only has trailing (after) context."""
        source = "line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing lines 1-2 but has line3 onwards
        working = "different1\ndifferent2\nline3\nline4\nline5\n"

        # Claim line 2 - no leading context but has trailing (line3)
        claimed = ["2"]

        # Should succeed - line3 provides trailing context
        result = merge_batch(source, BatchOwnership(claimed, []), working)
        assert "line2" in result

    def test_merge_succeeds_with_only_leading_context(self):
        """Test merge with missing line that only has leading (before) context."""
        source = "line1\nline2\nline3\nline4\nline5\n"

        # Working tree has line1-3 but then different content
        working = "line1\nline2\nline3\ndifferent4\ndifferent5\n"

        # Claim line 4 - has leading context (line3) but no trailing
        claimed = ["4"]

        # Should succeed - line3 provides leading context
        result = merge_batch(source, BatchOwnership(claimed, []), working)
        assert "line4" in result

    def test_merge_requires_context_even_at_edges(self):
        """Test that edge lines require context too (no special case)."""
        source = "line1\nline2\nline3\n"

        # Working tree has middle line only
        working = "different1\nline2\ndifferent3\n"

        # Claim first line - has context (line2 is aligned)
        claimed = ["1"]

        # Should succeed - line2 provides context
        result = merge_batch(source, BatchOwnership(claimed, []), working)
        assert "line1" in result

    def test_merge_edge_lines_fail_without_neighbors(self):
        """Test that edge lines fail when completely isolated."""
        source = "line1\nline2\nline3\n"

        # Working tree completely different
        working = "different1\ndifferent2\ndifferent3\n"

        # Claim first line with no aligned neighbors
        claimed = ["1"]

        # Should fail - file completely rewritten
        with pytest.raises(MergeError, match="file completely rewritten"):
            merge_batch(source, BatchOwnership(claimed, []), working)


class TestDiscardBatch:
    """Tests for discard_batch function (inverse of merge_batch)."""

    def test_discard_simple_claimed_line(self):
        """Test discarding a single claimed line restores baseline."""
        baseline = "original\n"
        batch_source = "modified\n"
        working = "modified\n"

        # Claim the modified line
        ownership = BatchOwnership(["1"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore baseline
        assert result == "original\n"

    def test_discard_preserves_non_batch_content(self):
        """Test that non-batch content is preserved."""
        baseline = "line1\nline2\nline3\n"
        batch_source = "line1\nmodified2\nline3\n"
        working = "line1\nmodified2\nextra\nline3\n"

        # Claim only line 2 (modified2)
        ownership = BatchOwnership(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore line2 from baseline, keep extra line
        assert result == "line1\nline2\nextra\nline3\n"

    def test_discard_after_divergence(self):
        """Test discarding after working tree diverged from batch source."""
        baseline = "A\nB\nC\nD\nE\n"
        batch_source = "A\nB_modified\nC\nD\nE\n"
        # Working tree added lines at top
        working = "X\nY\nZ\nA\nB_modified\nC\nD\nE\n"

        # Claim the modified B
        ownership = BatchOwnership(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline, keep X, Y, Z
        assert result == "X\nY\nZ\nA\nB\nC\nD\nE\n"

    def test_discard_with_insertion(self):
        """Test discarding insertion removes it."""
        baseline = "line1\nline2\n"
        batch_source = "line1\nline2\n"
        working = "line1\ninserted\nline2\n"

        # Insertion after line 1
        blob_sha = create_git_blob(["inserted\n".encode()])
        ownership = BatchOwnership([], [{"after_source_line": 1, "blob": blob_sha}])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Insertion should be removed
        assert result == "line1\nline2\n"

    def test_discard_with_insertion_at_start(self):
        """Test discarding insertion at start of file."""
        baseline = "line1\nline2\n"
        batch_source = "line1\nline2\n"
        working = "inserted\nline1\nline2\n"

        # Insertion at start (after_source_line: None)
        blob_sha = create_git_blob(["inserted\n".encode()])
        ownership = BatchOwnership([], [{"after_source_line": None, "blob": blob_sha}])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Start insertion should be removed
        assert result == "line1\nline2\n"

    def test_discard_combined_claimed_and_insertion(self):
        """Test discarding both claimed lines and insertions."""
        baseline = "A\nB\nC\n"
        batch_source = "A\nB_modified\nC\n"
        working = "A\nB_modified\ninserted\nC\n"

        # Claim B and insert after B
        blob_sha = create_git_blob(["inserted\n".encode()])
        ownership = BatchOwnership(
            ["2"],
            [{"after_source_line": 2, "blob": blob_sha}]
        )

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline and remove insertion
        assert result == "A\nB\nC\n"

    def test_discard_multiple_insertions_same_position(self):
        """Test discarding multiple insertions at same position."""
        baseline = "line1\nline2\n"
        batch_source = "line1\nline2\n"
        working = "line1\ninsert1\ninsert2\nline2\n"

        # Two insertions after line 1
        blob1 = create_git_blob(["insert1\n".encode()])
        blob2 = create_git_blob(["insert2\n".encode()])
        ownership = BatchOwnership([], [
            {"after_source_line": 1, "blob": blob1},
            {"after_source_line": 1, "blob": blob2}
        ])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Both insertions removed
        assert result == "line1\nline2\n"

    def test_discard_interleaved_batch_restores_baseline(self):
        """Test discarding interleaved batch (related to even/odd pathological case)."""
        baseline = "1\n2\n3\n4\n5\n"
        batch_source = "1\n2_mod\n3\n4_mod\n5\n"
        working = "1\n2_mod\n3\n4_mod\n5\n"

        # Claim even lines (2, 4)
        ownership = BatchOwnership(["2", "4"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore even lines from baseline
        assert result == "1\n2\n3\n4\n5\n"

    def test_discard_insertion_not_present_does_nothing(self):
        """Test that discarding missing insertion doesn't affect working tree."""
        baseline = "line1\nline2\n"
        batch_source = "line1\nline2\n"
        working = "line1\nline2\n"  # Insertion not present

        # Define insertion that's not in working tree
        blob_sha = create_git_blob(["inserted\n".encode()])
        ownership = BatchOwnership([], [{"after_source_line": 1, "blob": blob_sha}])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged
        assert result == "line1\nline2\n"

    def test_discard_claimed_line_not_present_does_nothing(self):
        """Test that discarding missing claimed line doesn't affect working tree."""
        baseline = "line1\nline2\n"
        batch_source = "line1\nmodified2\n"
        working = "line1\nline2\n"  # Already at baseline

        # Claim line 2, but it's not present in working tree
        ownership = BatchOwnership(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged
        assert result == "line1\nline2\n"
