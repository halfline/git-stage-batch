"""Tests for file I/O utilities."""



from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.file_io import write_file_paths_file
from git_stage_batch.utils.file_io import append_file_path_to_file
from git_stage_batch.utils.file_io import remove_file_path_from_file

from git_stage_batch.utils.file_io import (
    append_lines_to_file,
    read_text_file_contents,
    write_text_file_contents,
)


class TestReadTextFileContents:
    """Tests for read_text_file_contents function."""

    def test_read_existing_file(self, tmp_path):
        """Test reading an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!\n", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == "Hello, World!\n"

    def test_read_nonexistent_file_returns_empty_string(self, tmp_path):
        """Test reading a nonexistent file returns empty string."""
        test_file = tmp_path / "nonexistent.txt"

        content = read_text_file_contents(test_file)

        assert content == ""

    def test_read_empty_file(self, tmp_path):
        """Test reading an empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == ""

    def test_read_multiline_file(self, tmp_path):
        """Test reading a file with multiple lines."""
        test_file = tmp_path / "multiline.txt"
        test_file.write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")

        content = read_text_file_contents(test_file)

        assert content == "Line 1\nLine 2\nLine 3\n"


class TestWriteTextFileContents:
    """Tests for write_text_file_contents function."""

    def test_write_creates_file(self, tmp_path):
        """Test that writing creates a new file."""
        test_file = tmp_path / "test.txt"

        write_text_file_contents(test_file, "Hello, World!")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Hello, World!"

    def test_write_creates_parent_directories(self, tmp_path):
        """Test that writing creates parent directories."""
        test_file = tmp_path / "subdir" / "nested" / "test.txt"

        write_text_file_contents(test_file, "Content")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Content"

    def test_write_overwrites_existing_file(self, tmp_path):
        """Test that writing overwrites existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Old content", encoding="utf-8")

        write_text_file_contents(test_file, "New content")

        assert test_file.read_text(encoding="utf-8") == "New content"

    def test_write_empty_string(self, tmp_path):
        """Test writing an empty string."""
        test_file = tmp_path / "empty.txt"

        write_text_file_contents(test_file, "")

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == ""


class TestAppendLinesToFile:
    """Tests for append_lines_to_file function."""

    def test_append_to_new_file(self, tmp_path):
        """Test appending to a new file."""
        test_file = tmp_path / "test.txt"

        append_lines_to_file(test_file, ["Line 1", "Line 2"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Line 1\nLine 2\n"

    def test_append_to_existing_file(self, tmp_path):
        """Test appending to an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Existing line\n", encoding="utf-8")

        append_lines_to_file(test_file, ["New line 1", "New line 2"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Existing line\nNew line 1\nNew line 2\n"

    def test_append_normalizes_newlines(self, tmp_path):
        """Test that appending normalizes line endings."""
        test_file = tmp_path / "test.txt"

        append_lines_to_file(test_file, ["Line with newline\n", "Line without"])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Line with newline\nLine without\n"

    def test_append_creates_parent_directories(self, tmp_path):
        """Test that appending creates parent directories."""
        test_file = tmp_path / "subdir" / "test.txt"

        append_lines_to_file(test_file, ["Line 1"])

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == "Line 1\n"

    def test_append_empty_list(self, tmp_path):
        """Test appending an empty list."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Existing\n", encoding="utf-8")

        append_lines_to_file(test_file, [])

        content = test_file.read_text(encoding="utf-8")
        assert content == "Existing\n"


class TestFilePathListManagement:
    """Tests for file path list management functions."""

    def test_read_file_paths_file_empty(self, tmp_path):
        """Test reading an empty file paths file."""

        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        result = read_file_paths_file(path)
        assert result == []

    def test_read_file_paths_file_nonexistent(self, tmp_path):
        """Test reading a nonexistent file paths file."""

        path = tmp_path / "nonexistent.txt"
        result = read_file_paths_file(path)
        assert result == []

    def test_write_file_paths_file(self, tmp_path):
        """Test writing file paths to a file."""

        path = tmp_path / "paths.txt"
        file_paths = ["path/to/file1.txt", "path/to/file2.txt", "another/file.py"]
        write_file_paths_file(path, file_paths)

        # Read back and verify sorted and deduplicated
        result = read_file_paths_file(path)
        assert result == sorted(file_paths)

    def test_write_file_paths_file_deduplicates(self, tmp_path):
        """Test that write_file_paths_file deduplicates entries."""

        path = tmp_path / "paths.txt"
        file_paths = ["file1.txt", "file2.txt", "file1.txt", "file3.txt", "file2.txt"]
        write_file_paths_file(path, file_paths)

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_file_path_to_file(self, tmp_path):
        """Test appending a file path to a list."""

        path = tmp_path / "paths.txt"
        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file3.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt", "file3.txt"]

    def test_append_file_path_to_file_no_duplicates(self, tmp_path):
        """Test that appending doesn't create duplicates."""

        path = tmp_path / "paths.txt"
        append_file_path_to_file(path, "file1.txt")
        append_file_path_to_file(path, "file2.txt")
        append_file_path_to_file(path, "file1.txt")  # Duplicate

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]

    def test_remove_file_path_from_file(self, tmp_path):
        """Test removing a file path from a list."""

        path = tmp_path / "paths.txt"
        write_file_paths_file(path, ["file1.txt", "file2.txt", "file3.txt"])
        remove_file_path_from_file(path, "file2.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file3.txt"]

    def test_remove_file_path_from_file_nonexistent(self, tmp_path):
        """Test removing a nonexistent file path doesn't error."""

        path = tmp_path / "paths.txt"
        write_file_paths_file(path, ["file1.txt", "file2.txt"])
        remove_file_path_from_file(path, "nonexistent.txt")

        result = read_file_paths_file(path)
        assert result == ["file1.txt", "file2.txt"]
