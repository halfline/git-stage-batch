"""Tests for data models."""

import pytest

from git_stage_batch.models import (
    CurrentLines,
    HunkHeader,
    LineEntry,
    SingleHunkPatch,
)


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


class TestLineEntry:
    """Tests for LineEntry dataclass."""

    def test_context_line_creation(self):
        """Test creating a context line (unchanged)."""
        line = LineEntry(
            id=None,
            kind=" ",
            old_line_number=10,
            new_line_number=10,
            text="unchanged line"
        )

        assert line.id is None
        assert line.kind == " "
        assert line.old_line_number == 10
        assert line.new_line_number == 10
        assert line.text == "unchanged line"

    def test_added_line_creation(self):
        """Test creating an added line (+)."""
        line = LineEntry(
            id=1,
            kind="+",
            old_line_number=None,
            new_line_number=15,
            text="new line"
        )

        assert line.id == 1
        assert line.kind == "+"
        assert line.old_line_number is None
        assert line.new_line_number == 15
        assert line.text == "new line"

    def test_deleted_line_creation(self):
        """Test creating a deleted line (-)."""
        line = LineEntry(
            id=2,
            kind="-",
            old_line_number=10,
            new_line_number=None,
            text="old line"
        )

        assert line.id == 2
        assert line.kind == "-"
        assert line.old_line_number == 10
        assert line.new_line_number is None
        assert line.text == "old line"


class TestCurrentLines:
    """Tests for CurrentLines dataclass."""

    @pytest.fixture
    def sample_hunk(self):
        """Create a sample hunk for testing."""
        header = HunkHeader(old_start=5, old_len=4, new_start=5, new_len=4)
        lines = [
            LineEntry(None, " ", 5, 5, "context line 1"),
            LineEntry(1, "-", 6, None, "old line"),
            LineEntry(2, "+", None, 6, "new line"),
            LineEntry(None, " ", 7, 7, "context line 2"),
        ]
        return CurrentLines(path="test.py", header=header, lines=lines)

    def test_current_lines_creation(self, sample_hunk):
        """Test creating a CurrentLines object."""
        assert sample_hunk.path == "test.py"
        assert sample_hunk.header.old_start == 5
        assert len(sample_hunk.lines) == 4

    def test_changed_line_ids(self, sample_hunk):
        """Test getting changed line IDs."""
        changed_ids = sample_hunk.changed_line_ids()
        assert changed_ids == [1, 2]

    def test_changed_line_ids_empty(self):
        """Test changed_line_ids with no changed lines."""
        header = HunkHeader(1, 2, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "context 1"),
            LineEntry(None, " ", 2, 2, "context 2"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        assert current_lines.changed_line_ids() == []

    def test_changed_line_ids_only_additions(self):
        """Test changed_line_ids with only additions."""
        header = HunkHeader(1, 0, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, "new line 1"),
            LineEntry(2, "+", None, 2, "new line 2"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        assert current_lines.changed_line_ids() == [1, 2]

    def test_changed_line_ids_only_deletions(self):
        """Test changed_line_ids with only deletions."""
        header = HunkHeader(1, 2, 1, 0)
        lines = [
            LineEntry(1, "-", 1, None, "deleted line 1"),
            LineEntry(2, "-", 2, None, "deleted line 2"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        assert current_lines.changed_line_ids() == [1, 2]

    def test_maximum_line_id_digit_count_single_digit(self, sample_hunk):
        """Test maximum_line_id_digit_count with single-digit IDs."""
        assert sample_hunk.maximum_line_id_digit_count() == 1

    def test_maximum_line_id_digit_count_multi_digit(self):
        """Test maximum_line_id_digit_count with multi-digit IDs."""
        header = HunkHeader(1, 15, 1, 15)
        lines = [
            LineEntry(1, "+", None, 1, "line 1"),
            LineEntry(10, "+", None, 2, "line 10"),
            LineEntry(100, "+", None, 3, "line 100"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        assert current_lines.maximum_line_id_digit_count() == 3

    def test_maximum_line_id_digit_count_no_changes(self):
        """Test maximum_line_id_digit_count with no changed lines."""
        header = HunkHeader(1, 2, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "context"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        assert current_lines.maximum_line_id_digit_count() == 1


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
