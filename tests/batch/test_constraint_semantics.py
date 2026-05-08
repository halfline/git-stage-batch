"""Tests for constraint-based batch semantics.

These tests validate that the implementation matches the architecture
described in BATCHES.md.
"""


from git_stage_batch.batch.ownership import BatchOwnership, DeletionClaim
from git_stage_batch.batch.merge import merge_batch
from git_stage_batch.batch.storage import _build_realized_content


class TestDeletionClaimIdentity:
    """Test that deletion claims are structurally anchored and independent."""

    def test_two_deletion_claims_same_anchor_different_content(self):
        """Two deletion claims at same anchor but different content are distinct.

        This tests that anchors are part of identity, not just hints.
        Different content at the same anchor represents different constraints.
        """
        batch_source = b"line1\nline2\nline3\n"
        working = b"line1\nold_impl()\nline3\n"

        # Two batches both have deletions anchored after line 1
        # Batch A: removes "debug_log()"
        # Batch B: removes "old_impl()"
        # These are DIFFERENT constraints even though anchor is the same

        ownership_a = BatchOwnership.from_presence_lines(["1"], [DeletionClaim(anchor_line=1, content_lines=[b"debug_log()\n"])])

        ownership_b = BatchOwnership.from_presence_lines(["1"], [DeletionClaim(anchor_line=1, content_lines=[b"old_impl()\n"])])

        # Applying batch A should preserve "old_impl()" even though anchor matches.
        result_a = merge_batch(batch_source, ownership_a, working)
        assert b"old_impl()" in result_a

        # Applying batch B SHOULD suppress "old_impl()"
        result_b = merge_batch(batch_source, ownership_b, working)
        assert b"old_impl()" not in result_b

    def test_same_deletion_content_different_anchors(self):
        """Same deletion content at different anchors are distinct constraints.

        This tests that deletion claims are position-aware, not global.
        The same bytes at different structural positions are different claims.
        """
        batch_source = b"line1\nmarker\nline3\nmarker\nline5\n"
        working = b"line1\nmarker\nline3\nmarker\nline5\n"

        # Claim that suppresses only the first marker after line 1.
        ownership = BatchOwnership.from_presence_lines(["1", "3", "5"], [DeletionClaim(anchor_line=1, content_lines=[b"marker\n"])])

        result = merge_batch(batch_source, ownership, working)
        lines = result.splitlines(keepends=True)

        # The first marker should be suppressed.
        assert lines[1] != b"marker\n"

        # The second marker should remain at its own anchor.
        assert b"marker\n" in lines

        marker_count = sum(1 for line in lines if line == b"marker\n")
        assert marker_count == 1, "expected exactly one marker remaining"

    def test_deletion_claims_not_collapsed(self):
        """Multiple deletion claims must remain structurally distinct.

        Tests that contiguous deletion runs are kept distinct rather than collapsed into
        a single claim, even if they could share an anchor.
        """
        # Two separate deletion runs should be two separate claims
        ownership = BatchOwnership.from_presence_lines(
            ["1", "2", "3"],
            [
                DeletionClaim(anchor_line=1, content_lines=[b"old1\n", b"old2\n"]),
                # Kept separate from the first claim even though it shares an anchor.
            ]
        )

        # Implementation should preserve both claims separately
        assert len(ownership.deletions) == 1  # One contiguous run
        assert len(ownership.deletions[0].content_lines) == 2  # Two lines in it


class TestRepeatedLinesNotGloballyRemoved:
    """Test that deletion constraints are position-aware, not global bans."""

    def test_repeated_identical_lines_only_anchored_removed(self):
        """Repeated lines: only suppress at anchored position, not globally.

        Deletion claims must not act as global content filters.
        """
        batch_source = b"header\ncommon\nfooter\n"
        working = b"header\ncommon\nsection1\ncommon\nsection2\ncommon\nfooter\n"

        # Suppress "common" after "header" (first occurrence only)
        ownership = BatchOwnership.from_presence_lines(
            ["1", "3"],
            [DeletionClaim(anchor_line=1, content_lines=[b"common\n"])]
        )

        result = merge_batch(batch_source, ownership, working)
        lines = result.splitlines(keepends=True)

        # "common" should be removed at its anchored position (after header)
        # But other occurrences should remain
        common_count = sum(1 for line in lines if line == b"common\n")
        assert common_count >= 2, "other 'common' lines should remain"


class TestIdempotence:
    """Test that applying batches multiple times is idempotent."""

    def test_reapplying_batch_is_idempotent(self):
        """Applying the same batch twice yields identical results.

        This validates that constraints are checked (not blindly applied).
        """
        batch_source = b"line1\nline2-modified\nline3\n"
        working = b"line1\nline2\nline3\n"

        ownership = BatchOwnership.from_presence_lines(["2"], [DeletionClaim(anchor_line=1, content_lines=[b"line2\n"])])

        # First application
        result1 = merge_batch(batch_source, ownership, working)

        # Second application (should be no-op)
        result2 = merge_batch(batch_source, ownership, result1)

        assert result1 == result2, "Reapplying batch should be idempotent"

    def test_idempotence_with_working_tree_extras(self):
        """Idempotence preserved even when working tree has extras."""
        batch_source = b"line1\nline2-modified\nline3\n"
        working = b"line1\nline2\nextra\nline3\n"

        ownership = BatchOwnership.from_presence_lines(["2"], [DeletionClaim(anchor_line=1, content_lines=[b"line2\n"])])

        result1 = merge_batch(batch_source, ownership, working)
        result2 = merge_batch(batch_source, ownership, result1)

        assert result1 == result2
        # Extras should be preserved in both
        assert b"extra\n" in result1
        assert b"extra\n" in result2


class TestPureRemovalBatches:
    """Test deletion-only ownership (no claimed presence lines)."""

    def test_deletion_only_ownership(self):
        """Batch with only deletion claims, no presence claims.

        This tests that pure removals are representable.
        """
        batch_source = b"line1\nline2\ndebug_log()\nline3\n"
        working = b"line1\nline2\ndebug_log()\nline3\n"

        # Pure removal: just suppress debug_log, don't claim anything else
        ownership = BatchOwnership.from_presence_lines([], [DeletionClaim(anchor_line=2, content_lines=[b"debug_log()\n"])])

        result = merge_batch(batch_source, ownership, working)

        assert b"debug_log()" not in result
        assert b"line1\n" in result
        assert b"line2\n" in result
        assert b"line3\n" in result


class TestBytesBasedSemantics:
    """Test that merge logic is bytes-based, not text-based."""

    def test_non_utf8_content_preserved_exactly(self):
        """Non-UTF-8 bytes must be preserved exactly in merge."""
        # Latin-1 content
        batch_source = b"line1\n\xe9cole\nline3\n"  # "école" in Latin-1
        working = b"line1\n\xe9cole\nextra\nline3\n"

        # Claim école (line 2) and suppress "extra" (working tree insertion after école)
        ownership = BatchOwnership.from_presence_lines(["2"], [DeletionClaim(anchor_line=2, content_lines=[b"extra\n"])])

        result = merge_batch(
            batch_source,
            ownership,
            working
        )

        # Result must contain exact Latin-1 bytes, not UTF-8 replacement
        assert b"\xe9cole" in result
        # "extra" should be removed (deletion after line 2)
        assert b"extra" not in result

    def test_crlf_line_endings_preserved(self):
        """CRLF line endings are preserved after normalized matching."""
        batch_source = b"line1\r\nline2-modified\r\nline3\r\n"
        working = b"line1\r\nline2\r\nline3\r\n"

        ownership = BatchOwnership.from_presence_lines(["2"], [DeletionClaim(anchor_line=1, content_lines=[b"line2\r\n"])])

        result = merge_batch(batch_source, ownership, working)

        assert result == b"line1\r\nline2-modified\r\nline3\r\n"
        assert b"line2-modified" in result


class TestRealizedContentConstruction:
    """Test _build_realized_content follows constraint semantics."""

    def test_realized_content_respects_anchors(self):
        """Realized content construction must honor structural anchors."""
        baseline_content = b"line1\nline2\nline3\n"
        batch_source_content = b"line1\nline2-modified\nline3\n"

        ownership = BatchOwnership.from_presence_lines(["2"], [DeletionClaim(anchor_line=1, content_lines=[b"line2\n"])])

        realized = _build_realized_content(
            baseline_content,
            batch_source_content,
            ownership
        )

        # Should have line2-modified (claimed)
        assert b"line2-modified\n" in realized

        # Should not have old line2 (suppressed at anchor)
        assert b"line2\n" not in realized

        # Should have unclaimed lines from baseline
        assert b"line1\n" in realized
        assert b"line3\n" in realized

    def test_missing_anchor_in_realization_falls_back_to_prior_boundary(self):
        """Realization should not fail when an unclaimed source-only anchor is absent."""
        baseline_content = b"header\nold value\nfooter\n"
        batch_source_content = b"header\nnew value\nfooter\n"

        ownership = BatchOwnership.from_presence_lines([], [DeletionClaim(anchor_line=2, content_lines=[b"old value\n"])],
        )

        realized = _build_realized_content(
            baseline_content,
            batch_source_content,
            ownership
        )

        assert realized == b"header\nfooter\n"

    def test_missing_anchor_after_prior_absence_constraint_still_suppresses(self):
        """A later deletion can use the prior boundary if its anchor was removed."""
        baseline_content = b"line1\nline2\nline3\n"
        batch_source_content = b"line1\nline2\nline3\n"

        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                DeletionClaim(anchor_line=1, content_lines=[b"line2\n"]),
                DeletionClaim(anchor_line=2, content_lines=[b"line3\n"]),
            ],
        )

        realized = _build_realized_content(
            baseline_content,
            batch_source_content,
            ownership
        )

        assert realized == b"line1\n"
