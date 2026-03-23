"""Tests for show command."""

import subprocess

import pytest

from git_stage_batch.commands.show import command_show


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


class TestCommandShow:
    """Tests for show command."""

    def test_show_displays_hunk(self, temp_git_repo, capsys):
        """Test that show displays a hunk when changes exist."""
        # Modify the existing README.md file
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line added\n")

        command_show()

        captured = capsys.readouterr()
        assert "--- a/README.md" in captured.out
        assert "+++ b/README.md" in captured.out
        assert "+New line added" in captured.out

    def test_show_no_changes(self, temp_git_repo, capsys):
        """Test that show displays message when no changes exist."""
        command_show()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out

    def test_show_only_first_hunk(self, temp_git_repo, capsys):
        """Test that show only displays the first hunk when multiple exist."""
        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Now modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        command_show()

        captured = capsys.readouterr()
        # Should show file1 but not file2
        assert "file1.txt" in captured.out
        assert "file2.txt" not in captured.out

    def test_show_skips_blocked_hunks(self, temp_git_repo, capsys):
        """Test that show skips hunks in the blocklist."""
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.core.diff_parser import parse_unified_diff_streaming
        from git_stage_batch.utils.git import stream_git_command
        from git_stage_batch.utils.paths import ensure_state_directory_exists, get_block_list_file_path, get_context_lines

        # Create and commit two files
        file1 = temp_git_repo / "file1.txt"
        file1.write_text("original 1\n")
        file2 = temp_git_repo / "file2.txt"
        file2.write_text("original 2\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        file1.write_text("modified 1\n")
        file2.write_text("modified 2\n")

        # Get the hash of the first hunk and block it
        ensure_state_directory_exists()
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        first_patch_hash = compute_stable_hunk_hash(patches[0].to_patch_text())

        blocklist_path = get_block_list_file_path()
        blocklist_path.write_text(f"{first_patch_hash}\n")

        command_show()

        captured = capsys.readouterr()
        # Should skip file1 and show file2
        assert "file1.txt" not in captured.out
        assert "file2.txt" in captured.out

    def test_show_all_hunks_blocked(self, temp_git_repo, capsys):
        """Test that show displays message when all hunks are blocked."""
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.core.diff_parser import parse_unified_diff_streaming
        from git_stage_batch.utils.git import stream_git_command
        from git_stage_batch.utils.paths import ensure_state_directory_exists, get_block_list_file_path, get_context_lines

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified line\n")

        # Get the hash and block it
        ensure_state_directory_exists()
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        patch_hash = compute_stable_hunk_hash(patches[0].to_patch_text())

        blocklist_path = get_block_list_file_path()
        blocklist_path.write_text(f"{patch_hash}\n")

        command_show()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.out

    def test_show_caches_current_hunk_state(self, temp_git_repo, capsys):
        """Test that show caches the current hunk state files."""
        from git_stage_batch.core.hashing import compute_stable_hunk_hash
        from git_stage_batch.core.diff_parser import parse_unified_diff_streaming
        from git_stage_batch.utils.git import stream_git_command
        from git_stage_batch.utils.paths import (
            ensure_state_directory_exists,
            get_context_lines,
            get_current_hunk_hash_file_path,
            get_current_hunk_patch_file_path,
            get_current_lines_json_file_path,
            get_index_snapshot_file_path,
            get_working_tree_snapshot_file_path,
        )

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Get expected patch and hash
        ensure_state_directory_exists()
        patches = list(parse_unified_diff_streaming(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        expected_patch = patches[0].to_patch_text()
        expected_hash = compute_stable_hunk_hash(expected_patch)

        command_show()

        # Verify state files were written
        assert get_current_hunk_patch_file_path().exists()
        assert get_current_hunk_hash_file_path().exists()
        assert get_current_lines_json_file_path().exists()
        assert get_index_snapshot_file_path().exists()
        assert get_working_tree_snapshot_file_path().exists()

        # Verify patch content
        cached_patch = get_current_hunk_patch_file_path().read_text()
        assert cached_patch == expected_patch

        # Verify hash content
        cached_hash = get_current_hunk_hash_file_path().read_text()
        assert cached_hash == expected_hash
