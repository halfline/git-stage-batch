"""Tests for selected hunk recalculation."""

import io
import subprocess
import sys

import pytest

from git_stage_batch.core.hashing import compute_stable_hunk_hash_from_lines
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.selected_change.hunk_recalculation import (
    RecalculateSelectedHunkResult,
    recalculate_selected_hunk_for_file,
)
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.file_io import append_lines_to_file
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_block_list_file_path,
    get_processed_include_ids_file_path,
    get_selected_hunk_patch_file_path,
)
from tests.diff_parser_helpers import collect_unified_diff


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    ensure_state_directory_exists()

    return repo


class TestRecalculateCurrentHunkForFile:
    """Tests for recalculate_selected_hunk_for_file()."""

    def test_recalculates_hunk_after_modification(self, temp_git_repo):
        """Test that hunk is recalculated after file modification."""
        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("changed1\nchanged2\nchanged3\n")

        # Cache initial hunk
        initial_lines = fetch_next_change()
        assert initial_lines is not None

        # Partially modify the file (simulate line-level operation)
        test_file.write_text("changed1\nline2\nchanged3\n")

        # Recalculate
        captured = io.StringIO()
        sys.stdout = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stdout = sys.__stdout__

        # Should have updated the cached hunk
        assert get_selected_hunk_patch_file_path().exists()
        new_patch = get_selected_hunk_patch_file_path().read_text()
        assert "test.txt" in new_patch

    def test_clears_processed_ids(self, temp_git_repo):
        """Test that processed IDs are cleared when recalculating."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("modified\n")

        # Cache hunk and add some processed IDs
        fetch_next_change()
        get_processed_include_ids_file_path().write_text("1\n2\n")

        # Recalculate
        captured = io.StringIO()
        sys.stdout = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stdout = sys.__stdout__

        # Processed IDs should be cleared
        if get_processed_include_ids_file_path().exists():
            content = get_processed_include_ids_file_path().read_text()
            assert content.strip() == ""

    def test_skips_blocked_hunks(self, temp_git_repo):
        """Test that blocked hunks are skipped during recalculation."""

        # Create a file with change
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(
            ["git", "add", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add file"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        test_file.write_text("modified\n")

        # Get the hunk hash and block it
        result = subprocess.run(
            ["git", "diff", "--no-color", "test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        stdout_bytes = (
            result.stdout
            if isinstance(result.stdout, bytes)
            else result.stdout.encode("utf-8")
        )
        patches = list(collect_unified_diff(stdout_bytes.splitlines(keepends=True)))
        hunk_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        append_lines_to_file(get_block_list_file_path(), [hunk_hash])

        # Initialize session state
        initialize_abort_state()

        # Try to recalculate - should find no hunks
        captured = io.StringIO()
        sys.stderr = captured
        try:
            recalculate_selected_hunk_for_file("test.txt")
        finally:
            sys.stderr = sys.__stderr__

        output = captured.getvalue()
        assert (
            "No pending hunks" in output
            or not get_selected_hunk_patch_file_path().exists()
        )

    def test_reports_next_change_when_file_is_exhausted(self, temp_git_repo):
        """Recalculation should not call the show command for the next file."""
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("base 1\n")
        file2.write_text("base 2\n")
        subprocess.run(
            ["git", "add", "file1.txt", "file2.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Add files"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        file1.write_text("base 1\nselected\n")
        file2.write_text("base 2\nnext\n")
        selected = fetch_next_change()
        assert selected.path == "file1.txt"

        file1.write_text("base 1\n")

        result = recalculate_selected_hunk_for_file(
            "file1.txt",
            auto_advance=True,
        )

        assert result is RecalculateSelectedHunkResult.SHOW_NEXT_CHANGE
        assert not get_selected_hunk_patch_file_path().exists()
