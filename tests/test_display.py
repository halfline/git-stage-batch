"""Tests for display and printing functionality."""

import subprocess

import pytest

from git_stage_batch.display import print_annotated_hunk_with_aligned_gutter
from git_stage_batch.line_selection import write_line_ids_file
from git_stage_batch.models import CurrentLines, HunkHeader, LineEntry
from git_stage_batch.state import (
    ensure_state_directory_exists,
    get_processed_exclude_ids_file_path,
    get_processed_include_ids_file_path,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "test.txt").write_text("line1\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestPrintAnnotatedHunkWithAlignedGutter:
    """Tests for print_annotated_hunk_with_aligned_gutter."""

    def test_simple_hunk_output(self, temp_git_repo, capsys):
        """Test basic hunk printing."""
        ensure_state_directory_exists()

        header = HunkHeader(old_start=5, old_len=4, new_start=5, new_len=4)
        lines = [
            LineEntry(None, " ", 5, 5, "context line 1"),
            LineEntry(1, "-", 6, None, "old line"),
            LineEntry(2, "+", None, 6, "new line"),
            LineEntry(None, " ", 7, 7, "context line 2"),
        ]
        current_lines = CurrentLines(path="test.py", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        assert len(output_lines) == 5  # header + 4 lines
        assert output_lines[0] == "test.py :: @@ -5,4 +5,4 @@"
        assert "[#1]" in output_lines[2]
        assert "[#2]" in output_lines[3]

    def test_context_lines_no_label(self, temp_git_repo, capsys):
        """Test that context lines have no ID label."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "context 1"),
            LineEntry(1, "+", None, 2, "added"),
            LineEntry(None, " ", 2, 3, "context 2"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        # Context lines should have spaces instead of labels
        assert not output_lines[1].strip().startswith("[#")
        assert "[#1]" in output_lines[2]
        assert not output_lines[3].strip().startswith("[#")

    def test_alignment_single_digit(self, temp_git_repo, capsys):
        """Test gutter alignment with single-digit IDs."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(1, "+", None, 1, "line 1"),
            LineEntry(2, "+", None, 2, "line 2"),
            LineEntry(3, "+", None, 3, "line 3"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        # All labels should be aligned
        assert output_lines[1].startswith("[#1]")
        assert output_lines[2].startswith("[#2]")
        assert output_lines[3].startswith("[#3]")

    def test_alignment_multi_digit(self, temp_git_repo, capsys):
        """Test gutter alignment with multi-digit IDs."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 12, 1, 12)
        lines = [
            LineEntry(1, "+", None, 1, "line 1"),
            LineEntry(10, "+", None, 10, "line 10"),
            LineEntry(100, "+", None, 11, "line 100"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        # Extract the gutter parts (everything before the sign)
        gutter1 = output_lines[1].split(" + ")[0]
        gutter2 = output_lines[2].split(" + ")[0]
        gutter3 = output_lines[3].split(" + ")[0]

        # All gutters should have the same width
        assert len(gutter1) == len(gutter2) == len(gutter3)

    def test_sign_characters(self, temp_git_repo, capsys):
        """Test that sign characters are displayed correctly."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "context"),
            LineEntry(1, "-", 2, None, "deleted"),
            LineEntry(2, "+", None, 2, "added"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output = captured.out

        assert "   context" in output
        assert " - deleted" in output
        assert " + added" in output

    def test_header_format(self, temp_git_repo, capsys):
        """Test the header line format."""
        ensure_state_directory_exists()

        header = HunkHeader(old_start=10, old_len=5, new_start=15, new_len=7)
        lines = [LineEntry(None, " ", 10, 15, "test")]
        current_lines = CurrentLines(path="src/module.py", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        assert output_lines[0] == "src/module.py :: @@ -10,5 +15,7 @@"

    def test_only_additions(self, temp_git_repo, capsys):
        """Test hunk with only additions."""
        ensure_state_directory_exists()

        header = HunkHeader(0, 0, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, "new line 1"),
            LineEntry(2, "+", None, 2, "new line 2"),
        ]
        current_lines = CurrentLines(path="newfile.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        assert len(output_lines) == 3  # header + 2 lines
        assert "[#1]" in output_lines[1]
        assert "[#2]" in output_lines[2]

    def test_only_deletions(self, temp_git_repo, capsys):
        """Test hunk with only deletions."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 2, 0, 0)
        lines = [
            LineEntry(1, "-", 1, None, "deleted line 1"),
            LineEntry(2, "-", 2, None, "deleted line 2"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        assert len(output_lines) == 3  # header + 2 lines
        assert "[#1]" in output_lines[1]
        assert "[#2]" in output_lines[2]

    def test_empty_text_lines(self, temp_git_repo, capsys):
        """Test lines with empty text content."""
        ensure_state_directory_exists()

        header = HunkHeader(1, 2, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, ""),  # Empty added line
            LineEntry(None, " ", 1, 2, ""),  # Empty context line
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        output_lines = captured.out.splitlines()

        assert len(output_lines) == 3  # header + 2 lines

    def test_with_processed_ids(self, temp_git_repo, capsys):
        """Test that processed IDs are read (though not currently displayed differently)."""
        ensure_state_directory_exists()

        # Write some processed IDs
        write_line_ids_file(get_processed_include_ids_file_path(), [1])
        write_line_ids_file(get_processed_exclude_ids_file_path(), [2])

        header = HunkHeader(1, 4, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, "included"),
            LineEntry(2, "+", None, 2, "excluded"),
            LineEntry(3, "+", None, 3, "unprocessed"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        # Currently the function doesn't visually distinguish processed lines,
        # but it reads the files without error
        assert "[#1]" in captured.out
        assert "[#2]" in captured.out
        assert "[#3]" in captured.out

    def test_large_line_numbers(self, temp_git_repo, capsys):
        """Test with large line numbers."""
        ensure_state_directory_exists()

        header = HunkHeader(1000, 2, 1000, 2)
        lines = [
            LineEntry(1, "-", 1000, None, "old line"),
            LineEntry(2, "+", None, 1000, "new line"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        print_annotated_hunk_with_aligned_gutter(current_lines)

        captured = capsys.readouterr()
        assert "file.txt :: @@ -1000,2 +1000,2 @@" in captured.out
