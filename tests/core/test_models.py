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
            b"--- a/file.txt\n",
            b"+++ b/file.txt\n",
            b"@@ -1,3 +1,3 @@\n",
            b" context\n",
            b"-old line\n",
            b"+new line\n",
            b" context\n",
        ]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_bytes()
        expected = b"".join(lines)
        assert text == expected

    def test_to_patch_text_trailing_newline(self):
        """Test that to_patch_text always ends with a single newline."""
        lines = [b"--- a/file.txt\n", b"+++ b/file.txt\n", b"@@ -1 +1 @@\n", b"-old\n", b"+new\n"]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_bytes()
        assert text.endswith(b"\n")
        assert not text.endswith(b"\n\n")

    def test_to_patch_text_empty_lines(self):
        """Test to_patch_text with minimal patch."""
        lines = [b"--- a/file.txt\n", b"+++ b/file.txt\n", b"@@ -0,0 +1 @@\n", b"+new file\n"]
        patch = SingleHunkPatch(old_path="file.txt", new_path="file.txt", lines=lines)

        text = patch.to_patch_bytes()
        assert text == b"--- a/file.txt\n+++ b/file.txt\n@@ -0,0 +1 @@\n+new file\n"
