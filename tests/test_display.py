"""Tests for hunk display with line IDs."""

from io import StringIO
from unittest.mock import patch

from git_stage_batch.display import print_annotated_hunk_with_aligned_gutter
from git_stage_batch.models import CurrentLines, HunkHeader, LineEntry


class TestPrintAnnotatedHunkWithAlignedGutter:
    """Tests for print_annotated_hunk_with_aligned_gutter function."""

    def test_display_simple_hunk(self):
        """Test displaying a simple hunk with additions and deletions."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
        lines = [
            LineEntry(None, " ", 1, 1, "context"),
            LineEntry(1, "-", 2, None, "old line"),
            LineEntry(2, "+", None, 2, "new line"),
            LineEntry(None, " ", 3, 3, "context"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "test.txt :: @@ -1,3 +1,3 @@" in output
        assert "[#1] - old line" in output
        assert "[#2] + new line" in output
        assert "       context" in output

    def test_display_hunk_with_two_digit_ids(self):
        """Test displaying a hunk with two-digit line IDs."""
        header = HunkHeader(old_start=1, old_len=12, new_start=1, new_len=12)
        lines = [
            LineEntry(10, "+", None, 1, "line 10"),
            LineEntry(11, "+", None, 2, "line 11"),
            LineEntry(12, "+", None, 3, "line 12"),
        ]
        current_lines = CurrentLines(path="file.py", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        # Check that IDs are properly aligned with 2-digit width
        assert "[#10] + line 10" in output
        assert "[#11] + line 11" in output
        assert "[#12] + line 12" in output

    def test_display_hunk_with_three_digit_ids(self):
        """Test displaying a hunk with three-digit line IDs."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
        lines = [
            LineEntry(100, "+", None, 1, "line 100"),
            LineEntry(200, "+", None, 2, "line 200"),
            LineEntry(300, "+", None, 3, "line 300"),
        ]
        current_lines = CurrentLines(path="big.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        # Check that IDs are properly aligned with 3-digit width
        assert "[#100] + line 100" in output
        assert "[#200] + line 200" in output
        assert "[#300] + line 300" in output

    def test_display_hunk_only_context(self):
        """Test displaying a hunk with only context lines."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        lines = [
            LineEntry(None, " ", 1, 1, "line 1"),
            LineEntry(None, " ", 2, 2, "line 2"),
        ]
        current_lines = CurrentLines(path="unchanged.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "unchanged.txt :: @@ -1,2 +1,2 @@" in output
        assert "   line 1" in output
        assert "   line 2" in output
        # No IDs assigned
        assert "[#" not in output

    def test_display_hunk_new_file(self):
        """Test displaying a hunk for a new file (all additions)."""
        header = HunkHeader(old_start=0, old_len=0, new_start=1, new_len=2)
        lines = [
            LineEntry(1, "+", None, 1, "first line"),
            LineEntry(2, "+", None, 2, "second line"),
        ]
        current_lines = CurrentLines(path="new.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "new.txt :: @@ -0,0 +1,2 @@" in output
        assert "[#1] + first line" in output
        assert "[#2] + second line" in output

    def test_display_hunk_deleted_file(self):
        """Test displaying a hunk for a deleted file (all deletions)."""
        header = HunkHeader(old_start=1, old_len=2, new_start=0, new_len=0)
        lines = [
            LineEntry(1, "-", 1, None, "first line"),
            LineEntry(2, "-", 2, None, "second line"),
        ]
        current_lines = CurrentLines(path="deleted.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "deleted.txt :: @@ -1,2 +0,0 @@" in output
        assert "[#1] - first line" in output
        assert "[#2] - second line" in output

    def test_display_preserves_empty_lines(self):
        """Test that empty lines are displayed correctly."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        lines = [
            LineEntry(1, "+", None, 1, ""),
            LineEntry(None, " ", 2, 2, ""),
        ]
        current_lines = CurrentLines(path="empty.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "[#1] +" in output
        assert "      " in output

    def test_display_mixed_id_sizes_aligned(self):
        """Test that mixed single and double-digit IDs are aligned."""
        header = HunkHeader(old_start=1, old_len=4, new_start=1, new_len=4)
        lines = [
            LineEntry(5, "+", None, 1, "line 5"),
            LineEntry(12, "+", None, 2, "line 12"),
            LineEntry(3, "+", None, 3, "line 3"),
        ]
        current_lines = CurrentLines(path="mixed.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        # All IDs should be aligned based on max (12 = 2 digits)
        assert "[#5]  + line 5" in output
        assert "[#12] + line 12" in output
        assert "[#3]  + line 3" in output

    def test_gutter_alignment_with_varying_ids(self):
        """Test that gutter width adjusts correctly for different max ID sizes."""
        # With max ID = 1 digit, gutter is [#N] (4 chars)
        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        lines = [LineEntry(1, "+", None, 1, "test")]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_annotated_hunk_with_aligned_gutter(current_lines)
            output = fake_out.getvalue()

        assert "[#1] + test" in output
