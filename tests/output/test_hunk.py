"""Tests for hunk display with line IDs."""

from io import StringIO
from unittest.mock import patch

from git_stage_batch.core.models import LineLevelChange, HunkHeader, LineEntry
from git_stage_batch.output.colors import Colors
from git_stage_batch.output.hunk import print_line_level_changes


class TestPrintAnnotatedHunkWithAlignedGutter:
    """Tests for print_line_level_changes function."""

    def test_display_simple_hunk(self):
        """Test displaying a simple hunk with additions and deletions."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"context", text="context"),
            LineEntry(1, "-", 2, None, text_bytes=b"old line", text="old line"),
            LineEntry(2, "+", None, 2, text_bytes=b"new line", text="new line"),
            LineEntry(None, " ", 3, 3, text_bytes=b"context", text="context"),
        ]
        line_changes = LineLevelChange(path="test.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        assert "test.txt :: @@ -1,3 +1,3 @@" in output
        assert "[#1] - old line" in output
        assert "[#2] + new line" in output
        assert "       context" in output

    def test_display_hunk_with_two_digit_ids(self):
        """Test displaying a hunk with two-digit line IDs."""
        header = HunkHeader(old_start=1, old_len=12, new_start=1, new_len=12)
        lines = [
            LineEntry(10, "+", None, 1, text_bytes=b"line 10", text="line 10"),
            LineEntry(11, "+", None, 2, text_bytes=b"line 11", text="line 11"),
            LineEntry(12, "+", None, 3, text_bytes=b"line 12", text="line 12"),
        ]
        line_changes = LineLevelChange(path="file.py", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        # Check that IDs are properly aligned with 2-digit width
        assert "[#10] + line 10" in output
        assert "[#11] + line 11" in output
        assert "[#12] + line 12" in output

    def test_display_hunk_with_three_digit_ids(self):
        """Test displaying a hunk with three-digit line IDs."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
        lines = [
            LineEntry(100, "+", None, 1, text_bytes=b"line 100", text="line 100"),
            LineEntry(200, "+", None, 2, text_bytes=b"line 200", text="line 200"),
            LineEntry(300, "+", None, 3, text_bytes=b"line 300", text="line 300"),
        ]
        line_changes = LineLevelChange(path="big.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        # Check that IDs are properly aligned with 3-digit width
        assert "[#100] + line 100" in output
        assert "[#200] + line 200" in output
        assert "[#300] + line 300" in output

    def test_display_hunk_only_context(self):
        """Test displaying a hunk with only context lines."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        lines = [
            LineEntry(None, " ", 1, 1, text_bytes=b"line 1", text="line 1"),
            LineEntry(None, " ", 2, 2, text_bytes=b"line 2", text="line 2"),
        ]
        line_changes = LineLevelChange(path="unchanged.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
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
            LineEntry(1, "+", None, 1, text_bytes=b"first line", text="first line"),
            LineEntry(2, "+", None, 2, text_bytes=b"second line", text="second line"),
        ]
        line_changes = LineLevelChange(path="new.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        assert "new.txt :: @@ -0,0 +1,2 @@" in output
        assert "[#1] + first line" in output
        assert "[#2] + second line" in output

    def test_display_hunk_deleted_file(self):
        """Test displaying a hunk for a deleted file (all deletions)."""
        header = HunkHeader(old_start=1, old_len=2, new_start=0, new_len=0)
        lines = [
            LineEntry(1, "-", 1, None, text_bytes=b"first line", text="first line"),
            LineEntry(2, "-", 2, None, text_bytes=b"second line", text="second line"),
        ]
        line_changes = LineLevelChange(path="deleted.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        assert "deleted.txt :: @@ -1,2 +0,0 @@" in output
        assert "[#1] - first line" in output
        assert "[#2] - second line" in output

    def test_display_preserves_empty_lines(self):
        """Test that empty lines are displayed correctly."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"", text=""),
            LineEntry(None, " ", 2, 2, text_bytes=b"", text=""),
        ]
        line_changes = LineLevelChange(path="empty.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        assert "[#1] +" in output
        assert "      " in output

    def test_display_mixed_id_sizes_aligned(self):
        """Test that mixed single and double-digit IDs are aligned."""
        header = HunkHeader(old_start=1, old_len=4, new_start=1, new_len=4)
        lines = [
            LineEntry(5, "+", None, 1, text_bytes=b"line 5", text="line 5"),
            LineEntry(12, "+", None, 2, text_bytes=b"line 12", text="line 12"),
            LineEntry(3, "+", None, 3, text_bytes=b"line 3", text="line 3"),
        ]
        line_changes = LineLevelChange(path="mixed.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        # All IDs should be aligned based on max (12 = 2 digits)
        assert "[#5]  + line 5" in output
        assert "[#12] + line 12" in output
        assert "[#3]  + line 3" in output

    def test_gutter_alignment_with_varying_ids(self):
        """Test that gutter width adjusts correctly for different max ID sizes."""
        # With max ID = 1 digit, gutter is [#N] (4 chars)
        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
        lines = [LineEntry(1, "+", None, 1, text_bytes=b"test", text="test")]
        line_changes = LineLevelChange(path="file.txt", header=header, lines=lines)

        with patch("sys.stdout", new=StringIO()) as fake_out:
            print_line_level_changes(line_changes)
            output = fake_out.getvalue()

        assert "[#1] + test" in output

    def test_gap_line_is_gray_when_color_enabled(self):
        """Synthetic omitted-context markers should be colored like the gutter."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
        lines = [
            LineEntry(1, "+", None, 1, text_bytes=b"first", text="first"),
            LineEntry(
                None,
                " ",
                None,
                None,
                text_bytes=b"... 2 more lines ...",
                text="... 2 more lines ...",
            ),
            LineEntry(2, "+", None, 4, text_bytes=b"last", text="last"),
        ]
        line_changes = LineLevelChange(path="gap.txt", header=header, lines=lines)

        with patch.object(Colors, "enabled", return_value=True):
            with patch("sys.stdout", new=StringIO()) as fake_out:
                print_line_level_changes(line_changes)
                output = fake_out.getvalue()

        assert f"{Colors.GRAY}   ... 2 more lines ...{Colors.RESET}" in output
