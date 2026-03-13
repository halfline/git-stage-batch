"""Tests for line-level infrastructure functions."""

import json
import subprocess

import pytest

from git_stage_batch.commands import (
    compute_remaining_changed_line_ids,
    convert_current_lines_to_serializable_dict,
    load_current_lines_from_state,
)
from git_stage_batch.models import CurrentLines, HunkHeader, LineEntry
from git_stage_batch.state import (
    CommandError,
    ensure_state_directory_exists,
    get_current_hunk_patch_file_path,
    get_current_lines_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
    write_text_file_contents,
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
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestConvertCurrentLinesToSerializableDict:
    """Tests for convert_current_lines_to_serializable_dict."""

    def test_convert_simple_addition(self, temp_git_repo):
        """Test converting a CurrentLines with a simple addition."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=3)
        lines = [
            LineEntry(id=None, kind=" ", old_line_number=1, new_line_number=1, text="line1\n"),
            LineEntry(id=1, kind="+", old_line_number=None, new_line_number=2, text="new line\n"),
            LineEntry(id=None, kind=" ", old_line_number=2, new_line_number=3, text="line2\n"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)

        result = convert_current_lines_to_serializable_dict(current_lines)

        assert result["path"] == "test.txt"
        assert result["header"]["old_start"] == 1
        assert result["header"]["old_len"] == 2
        assert result["header"]["new_start"] == 1
        assert result["header"]["new_len"] == 3
        assert len(result["lines"]) == 3
        assert result["lines"][0]["kind"] == " "
        assert result["lines"][1]["kind"] == "+"
        assert result["lines"][1]["id"] == 1
        assert result["lines"][1]["text"] == "new line\n"

    def test_convert_deletion(self, temp_git_repo):
        """Test converting a CurrentLines with a deletion."""
        header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=2)
        lines = [
            LineEntry(id=None, kind=" ", old_line_number=1, new_line_number=1, text="line1\n"),
            LineEntry(id=1, kind="-", old_line_number=2, new_line_number=None, text="deleted\n"),
            LineEntry(id=None, kind=" ", old_line_number=3, new_line_number=2, text="line2\n"),
        ]
        current_lines = CurrentLines(path="file.py", header=header, lines=lines)

        result = convert_current_lines_to_serializable_dict(current_lines)

        assert result["path"] == "file.py"
        assert result["lines"][1]["kind"] == "-"
        assert result["lines"][1]["id"] == 1
        assert result["lines"][1]["old_lineno"] == 2
        assert result["lines"][1]["new_lineno"] is None

    def test_convert_replacement(self, temp_git_repo):
        """Test converting a CurrentLines with replacement."""
        header = HunkHeader(old_start=1, old_len=2, new_start=1, new_len=2)
        lines = [
            LineEntry(id=1, kind="-", old_line_number=1, new_line_number=None, text="old\n"),
            LineEntry(id=2, kind="+", old_line_number=None, new_line_number=1, text="new\n"),
            LineEntry(id=None, kind=" ", old_line_number=2, new_line_number=2, text="same\n"),
        ]
        current_lines = CurrentLines(path="data.txt", header=header, lines=lines)

        result = convert_current_lines_to_serializable_dict(current_lines)

        assert len(result["lines"]) == 3
        assert result["lines"][0]["id"] == 1
        assert result["lines"][1]["id"] == 2

    def test_convert_is_json_serializable(self, temp_git_repo):
        """Test that result can be serialized to JSON."""
        header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=2)
        lines = [
            LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="added\n"),
        ]
        current_lines = CurrentLines(path="file.txt", header=header, lines=lines)

        result = convert_current_lines_to_serializable_dict(current_lines)

        # Should not raise
        json_str = json.dumps(result)
        assert isinstance(json_str, str)


class TestLoadCurrentLinesFromState:
    """Tests for load_current_lines_from_state."""

    def test_load_from_saved_state(self, temp_git_repo):
        """Test loading CurrentLines from saved JSON state."""
        ensure_state_directory_exists()

        # Create state files
        state_data = {
            "path": "test.py",
            "header": {
                "old_start": 10,
                "old_len": 3,
                "new_start": 10,
                "new_len": 4,
            },
            "lines": [
                {"id": None, "kind": " ", "old_lineno": 10, "new_lineno": 10, "text": "def foo():\n"},
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 11, "text": "    print('new')\n"},
                {"id": None, "kind": " ", "old_lineno": 11, "new_lineno": 12, "text": "    return\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "dummy patch")

        current_lines = load_current_lines_from_state()

        assert current_lines.path == "test.py"
        assert current_lines.header.old_start == 10
        assert current_lines.header.old_len == 3
        assert current_lines.header.new_start == 10
        assert current_lines.header.new_len == 4
        assert len(current_lines.lines) == 3
        assert current_lines.lines[1].id == 1
        assert current_lines.lines[1].kind == "+"
        assert current_lines.lines[1].text == "    print('new')\n"

    def test_load_requires_patch_file(self, temp_git_repo):
        """Test that loading requires current-hunk-patch file."""
        ensure_state_directory_exists()

        # Create JSON but not patch
        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 1},
            "lines": [],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))

        # Should return None when patch file is missing
        assert load_current_lines_from_state() is None

    def test_load_requires_json_file(self, temp_git_repo):
        """Test that loading requires current-lines.json file."""
        ensure_state_directory_exists()

        # Create patch but not JSON
        write_text_file_contents(get_current_hunk_patch_file_path(), "dummy")

        # Should return None when JSON file is missing
        assert load_current_lines_from_state() is None

    def test_load_with_multiple_changed_lines(self, temp_git_repo):
        """Test loading state with multiple changed lines."""
        ensure_state_directory_exists()

        state_data = {
            "path": "multi.txt",
            "header": {"old_start": 1, "old_len": 2, "new_start": 1, "new_len": 4},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "new1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "new2\n"},
                {"id": None, "kind": " ", "old_lineno": 1, "new_lineno": 3, "text": "old\n"},
                {"id": 3, "kind": "+", "old_lineno": None, "new_lineno": 4, "text": "new3\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")

        current_lines = load_current_lines_from_state()

        assert len(current_lines.lines) == 4
        assert current_lines.changed_line_ids() == [1, 2, 3]


class TestComputeRemainingChangedLineIds:
    """Tests for compute_remaining_changed_line_ids."""

    def test_compute_all_remaining_when_none_processed(self, temp_git_repo):
        """Test computing remaining IDs when nothing has been processed."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 2, "new_start": 1, "new_len": 4},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line2\n"},
                {"id": 3, "kind": "+", "old_lineno": None, "new_lineno": 3, "text": "line3\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == [1, 2, 3]

    def test_compute_remaining_after_include(self, temp_git_repo):
        """Test computing remaining IDs after some lines are included."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 4},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line2\n"},
                {"id": 3, "kind": "+", "old_lineno": None, "new_lineno": 3, "text": "line3\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_processed_include_ids_file_path(), "1\n2\n")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == [3]

    def test_compute_remaining_after_skip(self, temp_git_repo):
        """Test computing remaining IDs after some lines are skipped."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 3},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line2\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_processed_skip_ids_file_path(), "1\n")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == [2]

    def test_compute_remaining_after_mixed_operations(self, temp_git_repo):
        """Test computing remaining IDs after both include and skip."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 5},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line2\n"},
                {"id": 3, "kind": "+", "old_lineno": None, "new_lineno": 3, "text": "line3\n"},
                {"id": 4, "kind": "+", "old_lineno": None, "new_lineno": 4, "text": "line4\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_processed_include_ids_file_path(), "1\n3\n")
        write_text_file_contents(get_processed_skip_ids_file_path(), "2\n")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == [4]

    def test_compute_remaining_when_all_processed(self, temp_git_repo):
        """Test computing remaining IDs when all lines are processed."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 3},
            "lines": [
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line1\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line2\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_processed_include_ids_file_path(), "1\n")
        write_text_file_contents(get_processed_skip_ids_file_path(), "2\n")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == []

    def test_compute_remaining_returns_sorted_list(self, temp_git_repo):
        """Test that remaining IDs are returned in sorted order."""
        ensure_state_directory_exists()

        state_data = {
            "path": "file.txt",
            "header": {"old_start": 1, "old_len": 1, "new_start": 1, "new_len": 6},
            "lines": [
                {"id": 5, "kind": "+", "old_lineno": None, "new_lineno": 1, "text": "line5\n"},
                {"id": 1, "kind": "+", "old_lineno": None, "new_lineno": 2, "text": "line1\n"},
                {"id": 3, "kind": "+", "old_lineno": None, "new_lineno": 3, "text": "line3\n"},
                {"id": 2, "kind": "+", "old_lineno": None, "new_lineno": 4, "text": "line2\n"},
            ],
        }
        write_text_file_contents(get_current_lines_json_file_path(), json.dumps(state_data))
        write_text_file_contents(get_current_hunk_patch_file_path(), "patch")
        write_text_file_contents(get_processed_include_ids_file_path(), "5\n")

        remaining = compute_remaining_changed_line_ids()

        assert remaining == [1, 2, 3]  # Sorted despite unsorted input
