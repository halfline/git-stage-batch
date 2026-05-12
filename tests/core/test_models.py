"""Tests for data models."""


from git_stage_batch.core.models import HunkHeader, SingleHunkPatch


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

    def test_old_prefix_line_count_for_non_empty_range(self):
        """Non-empty old ranges start at the first changed old line."""
        header = HunkHeader(old_start=10, old_len=3, new_start=12, new_len=5)

        assert header.old_prefix_line_count() == 9

    def test_old_prefix_line_count_for_zero_length_range(self):
        """Zero-length old ranges are anchored after old_start."""
        header = HunkHeader(old_start=10, old_len=0, new_start=11, new_len=2)

        assert header.old_prefix_line_count() == 10

    def test_new_prefix_line_count_for_non_empty_range(self):
        """Non-empty new ranges start at the first changed new line."""
        header = HunkHeader(old_start=10, old_len=5, new_start=12, new_len=3)

        assert header.new_prefix_line_count() == 11

    def test_new_prefix_line_count_for_zero_length_range(self):
        """Zero-length new ranges are anchored after new_start."""
        header = HunkHeader(old_start=10, old_len=2, new_start=9, new_len=0)

        assert header.new_prefix_line_count() == 9


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
