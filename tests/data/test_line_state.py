"""Tests for line-level state management."""

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.core.models import LineLevelChange, HunkHeader, LineEntry
from git_stage_batch.data.line_state import (
    compute_remaining_changed_line_ids,
    convert_line_changes_to_serializable_dict,
    load_line_changes_from_state,
)
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_selected_hunk_patch_file_path,
    get_line_changes_json_file_path,
    get_processed_include_ids_file_path,
    get_processed_skip_ids_file_path,
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

    # Ensure state directory exists
    ensure_state_directory_exists()

    return repo


@pytest.fixture
def sample_line_changes():
    """Create a sample LineLevelChange object for testing."""
    header = HunkHeader(old_start=1, old_len=3, new_start=1, new_len=3)
    lines = [
        LineEntry(id=None, kind=" ", old_line_number=1, new_line_number=1, text="unchanged line"),
        LineEntry(id=1, kind="-", old_line_number=2, new_line_number=None, text="removed line"),
        LineEntry(id=2, kind="+", old_line_number=None, new_line_number=2, text="added line"),
        LineEntry(id=None, kind=" ", old_line_number=3, new_line_number=3, text="another unchanged"),
    ]
    return LineLevelChange(path="test.py", header=header, lines=lines)


class TestConvertLineLevelChangeToSerializableDict:
    """Tests for convert_line_changes_to_serializable_dict()."""

    def test_converts_line_changes_to_dict(self, sample_line_changes):
        """Test that LineLevelChange is properly converted to a dictionary."""
        result = convert_line_changes_to_serializable_dict(sample_line_changes)

        assert result["path"] == "test.py"
        assert result["header"]["old_start"] == 1
        assert result["header"]["old_len"] == 3
        assert result["header"]["new_start"] == 1
        assert result["header"]["new_len"] == 3
        assert len(result["lines"]) == 4

    def test_converts_line_entries(self, sample_line_changes):
        """Test that LineEntry objects are properly converted."""
        result = convert_line_changes_to_serializable_dict(sample_line_changes)

        # Check first line (unchanged)
        assert result["lines"][0]["id"] is None
        assert result["lines"][0]["kind"] == " "
        assert result["lines"][0]["old_lineno"] == 1
        assert result["lines"][0]["new_lineno"] == 1
        assert result["lines"][0]["text"] == "unchanged line"

        # Check removed line
        assert result["lines"][1]["id"] == 1
        assert result["lines"][1]["kind"] == "-"
        assert result["lines"][1]["old_lineno"] == 2
        assert result["lines"][1]["new_lineno"] is None

        # Check added line
        assert result["lines"][2]["id"] == 2
        assert result["lines"][2]["kind"] == "+"
        assert result["lines"][2]["old_lineno"] is None
        assert result["lines"][2]["new_lineno"] == 2

    def test_result_is_json_serializable(self, sample_line_changes):
        """Test that the result can be serialized to JSON."""
        result = convert_line_changes_to_serializable_dict(sample_line_changes)
        json_str = json.dumps(result)
        assert json_str is not None
        assert "test.py" in json_str


class TestLoadLineLevelChangeFromState:
    """Tests for load_line_changes_from_state()."""

    def test_returns_none_when_no_state_exists(self, temp_git_repo):
        """Test that None is returned when state files don't exist."""
        result = load_line_changes_from_state()
        assert result is None

    def test_returns_none_when_only_patch_exists(self, temp_git_repo):
        """Test that None is returned when only patch file exists."""
        patch_path = get_selected_hunk_patch_file_path()
        patch_path.write_text("dummy patch")

        result = load_line_changes_from_state()
        assert result is None

    def test_returns_none_when_only_json_exists(self, temp_git_repo):
        """Test that None is returned when only JSON file exists."""
        json_path = get_line_changes_json_file_path()
        json_path.write_text('{"path": "test.py"}')

        result = load_line_changes_from_state()
        assert result is None

    def test_loads_line_changes_from_state(self, temp_git_repo, sample_line_changes):
        """Test that LineLevelChange is properly loaded from state files."""
        # Write state files
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Load and verify
        result = load_line_changes_from_state()
        assert result is not None
        assert result.path == "test.py"
        assert result.header.old_start == 1
        assert result.header.old_len == 3
        assert len(result.lines) == 4

    def test_loads_line_entries_correctly(self, temp_git_repo, sample_line_changes):
        """Test that LineEntry objects are reconstructed correctly."""
        # Write state files
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Load and verify line entries
        result = load_line_changes_from_state()
        assert result.lines[0].id is None
        assert result.lines[0].kind == " "
        assert result.lines[0].text == "unchanged line"

        assert result.lines[1].id == 1
        assert result.lines[1].kind == "-"
        assert result.lines[1].old_line_number == 2
        assert result.lines[1].new_line_number is None

        assert result.lines[2].id == 2
        assert result.lines[2].kind == "+"
        assert result.lines[2].old_line_number is None
        assert result.lines[2].new_line_number == 2


class TestComputeRemainingChangedLineIds:
    """Tests for compute_remaining_changed_line_ids()."""

    def test_errors_when_no_selected_hunk(self, temp_git_repo):
        """Test that an error is raised when no selected hunk exists."""
        with pytest.raises(CommandError) as exc_info:
            compute_remaining_changed_line_ids()
        assert "No selected hunk" in str(exc_info.value.message)

    def test_returns_all_changed_ids_when_none_processed(self, temp_git_repo, sample_line_changes):
        """Test that all changed line IDs are returned when nothing has been processed."""
        # Set up state with no processed lines
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Create empty processed files
        get_processed_include_ids_file_path().write_text("")
        get_processed_skip_ids_file_path().write_text("")

        result = compute_remaining_changed_line_ids()
        # Changed lines are id 1 (removed) and id 2 (added)
        assert result == [1, 2]

    def test_excludes_included_lines(self, temp_git_repo, sample_line_changes):
        """Test that included lines are excluded from remaining IDs."""
        # Set up state
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Mark line 1 as included
        get_processed_include_ids_file_path().write_text("1\n")
        get_processed_skip_ids_file_path().write_text("")

        result = compute_remaining_changed_line_ids()
        # Only line 2 should remain
        assert result == [2]

    def test_excludes_skipped_lines(self, temp_git_repo, sample_line_changes):
        """Test that skipped lines are excluded from remaining IDs."""
        # Set up state
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Mark line 2 as skipped
        get_processed_include_ids_file_path().write_text("")
        get_processed_skip_ids_file_path().write_text("2\n")

        result = compute_remaining_changed_line_ids()
        # Only line 1 should remain
        assert result == [1]

    def test_excludes_both_included_and_skipped_lines(self, temp_git_repo, sample_line_changes):
        """Test that both included and skipped lines are excluded."""
        # Set up state
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(sample_line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Mark line 1 as included, line 2 as skipped
        get_processed_include_ids_file_path().write_text("1\n")
        get_processed_skip_ids_file_path().write_text("2\n")

        result = compute_remaining_changed_line_ids()
        # No lines should remain
        assert result == []

    def test_returns_sorted_ids(self, temp_git_repo):
        """Test that remaining IDs are returned in sorted order."""
        # Create a hunk with multiple changed lines
        header = HunkHeader(old_start=1, old_len=5, new_start=1, new_len=5)
        lines = [
            LineEntry(id=1, kind="+", old_line_number=None, new_line_number=1, text="added 1"),
            LineEntry(id=2, kind="-", old_line_number=1, new_line_number=None, text="removed 1"),
            LineEntry(id=3, kind="+", old_line_number=None, new_line_number=2, text="added 2"),
            LineEntry(id=4, kind="-", old_line_number=2, new_line_number=None, text="removed 2"),
            LineEntry(id=None, kind=" ", old_line_number=3, new_line_number=3, text="unchanged"),
        ]
        line_changes = LineLevelChange(path="test.py", header=header, lines=lines)

        # Set up state
        patch_path = get_selected_hunk_patch_file_path()
        json_path = get_line_changes_json_file_path()

        patch_path.write_text("dummy patch")
        serialized = convert_line_changes_to_serializable_dict(line_changes)
        write_text_file_contents(json_path, json.dumps(serialized))

        # Mark some as processed (not in order)
        get_processed_include_ids_file_path().write_text("3\n")
        get_processed_skip_ids_file_path().write_text("1\n")

        result = compute_remaining_changed_line_ids()
        # Should return [2, 4] in sorted order
        assert result == [2, 4]
