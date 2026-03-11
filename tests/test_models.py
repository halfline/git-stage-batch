"""Tests for data models."""

import pytest

from git_stage_batch.models import CurrentLines, HunkHeader, LineEntry, SingleHunkPatch


class TestHunkHeader:
    """Tests for HunkHeader dataclass."""

    def test_hunk_header_creation(self):
        """Test creating a HunkHeader."""
        header = HunkHeader(old_start=10, old_len=5, new_start=15, new_len=7)

        assert header.old_start == 10
        assert header.old_len == 5
        assert header.new_start == 15
        assert header.new_len == 7

    def test_hunk_header_equality(self):
        """Test HunkHeader equality."""
        header1 = HunkHeader(10, 5, 15, 7)
        header2 = HunkHeader(10, 5, 15, 7)
        header3 = HunkHeader(10, 5, 15, 8)

        assert header1 == header2
        assert header1 != header3


class TestSingleHunkPatch:
    """Tests for SingleHunkPatch dataclass."""

    def test_single_hunk_patch_creation(self):
        """Test creating a SingleHunkPatch."""
        lines = [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1,3 +1,3 @@",
            " context",
            "-old line",
            "+new line",
            " context",
        ]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        assert patch.old_path == "file.txt"
        assert patch.new_path == "file.txt"
        assert len(patch.lines) == 7

    def test_to_patch_text(self):
        """Test converting patch to text format."""
        lines = [
            "--- a/file.txt",
            "+++ b/file.txt",
            "@@ -1,3 +1,3 @@",
            " context",
            "-old line",
            "+new line",
            " context",
        ]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_text()
        expected = "\n".join(lines) + "\n"
        assert text == expected

    def test_to_patch_text_trailing_newline(self):
        """Test that to_patch_text always ends with a single newline."""
        lines = ["--- a/file.txt", "+++ b/file.txt", "@@ -1 +1 @@", "-old", "+new"]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_text()
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

    def test_to_patch_text_empty_lines(self):
        """Test to_patch_text with minimal patch."""
        lines = ["--- a/file.txt", "+++ b/file.txt", "@@ -0,0 +1 @@", "+new file"]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_text()
        assert text == "--- a/file.txt\n+++ b/file.txt\n@@ -0,0 +1 @@\n+new file\n"


class TestLineEntry:
    """Tests for LineEntry dataclass."""

    def test_line_entry_creation(self):
        """Test creating a LineEntry."""
        line = LineEntry(
            id=1,
            kind="+",
            old_line_number=None,
            new_line_number=5,
            text="new line",
        )

        assert line.id == 1
        assert line.kind == "+"
        assert line.old_line_number is None
        assert line.new_line_number == 5
        assert line.text == "new line"

    def test_line_entry_context_line(self):
        """Test creating a context line without an ID."""
        line = LineEntry(
            id=None,
            kind=" ",
            old_line_number=3,
            new_line_number=3,
            text="context",
        )

        assert line.id is None
        assert line.kind == " "
        assert line.old_line_number == 3
        assert line.new_line_number == 3


class TestCurrentLines:
    """Tests for CurrentLines dataclass."""

    def test_current_lines_creation(self):
        """Test creating a CurrentLines."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "context"),
            LineEntry(1, "-", 2, None, "old"),
            LineEntry(2, "+", None, 2, "new"),
        ]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        assert current.path == "file.txt"
        assert current.header == header
        assert len(current.lines) == 3

    def test_changed_line_ids(self):
        """Test getting changed line IDs."""
        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(None, " ", 1, 1, "context"),
            LineEntry(1, "-", 2, None, "old1"),
            LineEntry(2, "+", None, 2, "new1"),
            LineEntry(None, " ", 3, 3, "context"),
            LineEntry(3, "-", 4, None, "old2"),
        ]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        changed_ids = current.changed_line_ids()
        assert changed_ids == [1, 2, 3]

    def test_changed_line_ids_empty(self):
        """Test changed_line_ids with no changes."""
        header = HunkHeader(1, 2, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "context1"),
            LineEntry(None, " ", 2, 2, "context2"),
        ]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        assert current.changed_line_ids() == []

    def test_maximum_line_id_digit_count(self):
        """Test calculating maximum line ID digit count."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(5, "+", None, 1, "line"),
            LineEntry(12, "+", None, 2, "line"),
            LineEntry(100, "+", None, 3, "line"),
        ]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        # Max ID is 100, which has 3 digits
        assert current.maximum_line_id_digit_count() == 3

    def test_maximum_line_id_digit_count_single_digit(self):
        """Test digit count with single-digit IDs."""
        header = HunkHeader(1, 2, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, "line"),
            LineEntry(2, "+", None, 2, "line"),
        ]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        assert current.maximum_line_id_digit_count() == 1

    def test_maximum_line_id_digit_count_no_changes(self):
        """Test digit count with no changed lines."""
        header = HunkHeader(1, 1, 1, 1)
        lines = [LineEntry(None, " ", 1, 1, "context")]
        current = CurrentLines(path="file.txt", header=header, lines=lines)

        assert current.maximum_line_id_digit_count() == 1
