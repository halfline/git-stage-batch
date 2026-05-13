"""Tests for show command."""

from git_stage_batch.core.hashing import compute_stable_hunk_hash_from_lines
from tests.diff_parser_helpers import collect_unified_diff
from git_stage_batch.utils.git import stream_git_command
from git_stage_batch.utils.paths import get_block_list_file_path, get_context_lines
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists
from git_stage_batch.utils.paths import (
    get_selected_hunk_hash_file_path,
    get_selected_hunk_patch_file_path,
)

import subprocess

import pytest

import git_stage_batch.commands.show as show_module
from git_stage_batch.commands.show import command_show
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import get_selected_change_file_path
from git_stage_batch.data.line_state import load_line_changes_from_state


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

        command_start()
        command_show()

        captured = capsys.readouterr()
        assert "README.md" in captured.out
        assert "New line added" in captured.out
        assert "[#1]" in captured.out  # Check for line ID annotation

    def test_show_restores_previous_selection_when_hunk_is_fully_filtered(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Invisible filtered traversal should not replace the last selection."""
        a_file = temp_git_repo / "a.txt"
        b_file = temp_git_repo / "b.txt"
        a_file.write_text("a\n")
        b_file.write_text("b\n")
        subprocess.run(["git", "add", "a.txt", "b.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        a_file.write_text("a changed\n")
        b_file.write_text("b changed\n")

        command_start()
        capsys.readouterr()
        command_show(file="a.txt", porcelain=True)
        assert get_selected_change_file_path() == "a.txt"

        monkeypatch.setattr(show_module, "apply_line_level_batch_filter_to_cached_hunk", lambda: True)

        command_show()

        assert get_selected_change_file_path() == "a.txt"

    def test_show_no_changes(self, temp_git_repo, capsys):
        """Test that show displays message when no more hunks remain."""
        # Create a change and process it
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        command_start()
        # Show command itself doesn't consume hunks, but we need to simulate "no changes"
        # by having no unstaged changes
        subprocess.run(["git", "add", "-A"], check=True, cwd=temp_git_repo, capture_output=True)

        command_show()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

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

        command_start()
        command_show()

        captured = capsys.readouterr()
        # Should show file1 but not file2
        assert "file1.txt" in captured.out
        assert "file2.txt" not in captured.out

    def test_show_skips_blocked_hunks(self, temp_git_repo, capsys):
        """Test that show skips hunks in the blocklist."""

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

        # Start the session, then seed the iteration blocklist. Calling start
        # on an already-initialized session clears iteration state by design.
        command_start()
        capsys.readouterr()

        # Get the hash of the first hunk and block it
        patches = list(collect_unified_diff(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        first_patch_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        blocklist_path = get_block_list_file_path()
        blocklist_path.write_text(f"{first_patch_hash}\n")

        command_show()

        captured = capsys.readouterr()
        # Should skip file1 and show file2
        assert "file1.txt" not in captured.out
        assert "file2.txt" in captured.out

    def test_show_all_hunks_blocked(self, temp_git_repo, capsys):
        """Test that show displays message when all hunks are blocked."""

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified line\n")

        # Initialize session without caching a hunk
        ensure_state_directory_exists()
        initialize_abort_state()

        command_start()

        # Get the hash and block it
        patches = list(collect_unified_diff(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        patch_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        blocklist_path = get_block_list_file_path()
        blocklist_path.write_text(f"{patch_hash}\n")

        command_show()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_show_caches_selected_hunk_state(self, temp_git_repo, capsys):
        """Test that show caches the selected hunk patch and hash."""

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Initialize session
        ensure_state_directory_exists()
        initialize_abort_state()

        # Get expected patch and hash
        patches = list(collect_unified_diff(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        expected_patch = b"".join(patches[0].lines)
        expected_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        command_show()

        # Verify state files were written
        assert get_selected_hunk_patch_file_path().exists()
        assert get_selected_hunk_hash_file_path().exists()

        # Verify patch content
        cached_patch = get_selected_hunk_patch_file_path().read_bytes()
        assert cached_patch == expected_patch

        # Verify hash content
        cached_hash = get_selected_hunk_hash_file_path().read_text()
        assert cached_hash == expected_hash

    def test_show_porcelain_with_hunk(self, temp_git_repo, capsys):
        """Test that show --porcelain exits 0 with no output when hunk exists."""

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Initialize session without displaying anything
        ensure_state_directory_exists()
        initialize_abort_state()

        # Should exit normally (no exception) with no output
        command_show(porcelain=True)

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_show_porcelain_no_hunks(self, temp_git_repo):
        """Test that show --porcelain exits 1 when no hunks remain."""

        # Initialize session without changes
        ensure_state_directory_exists()
        initialize_abort_state()

        # No changes, should exit with code 1
        with pytest.raises(SystemExit) as exc_info:
            command_show(porcelain=True)

        assert exc_info.value.code == 1

    def test_show_porcelain_all_blocked(self, temp_git_repo):
        """Test that show --porcelain exits 1 when all hunks are blocked."""

        # Modify the README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Initialize session
        ensure_state_directory_exists()
        initialize_abort_state()

        # Block the hunk
        patches = list(collect_unified_diff(stream_git_command(["diff", f"-U{get_context_lines()}", "--no-color"])))
        patch_hash = compute_stable_hunk_hash_from_lines(patches[0].lines)

        blocklist_path = get_block_list_file_path()
        blocklist_path.write_text(f"{patch_hash}\n")

        # Should exit with code 1
        with pytest.raises(SystemExit) as exc_info:
            command_show(porcelain=True)

        assert exc_info.value.code == 1

    def test_show_displays_binary_change_in_hunk_mode(self, temp_git_repo, capsys):
        """Plain show should display binary changes instead of reporting no hunks."""
        binary_file = temp_git_repo / "asset.bin"
        binary_file.write_bytes(b"\x00\x01\x02")
        subprocess.run(["git", "add", "asset.bin"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add binary"], check=True, cwd=temp_git_repo, capture_output=True)
        binary_file.write_bytes(b"\x00\x03\x04")

        ensure_state_directory_exists()
        initialize_abort_state()

        command_show()

        captured = capsys.readouterr()
        assert "asset.bin" in captured.out
        assert "Binary file modified" in captured.out
        assert "No more hunks to process" not in captured.err

    def test_show_file_preview_hides_gutter_ids_without_replacing_selection(self, temp_git_repo, capsys):
        """Non-selectable file previews should hide gutter IDs and preserve cached selection."""
        file1 = temp_git_repo / "file1.txt"
        file2 = temp_git_repo / "file2.txt"
        file1.write_text("one\n")
        file2.write_text("two\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        file1.write_text("one changed\n")
        file2.write_text("two changed\n")

        command_start()
        capsys.readouterr()

        command_show(file="file1.txt", selectable=False)
        preview = capsys.readouterr()
        assert "file1.txt" in preview.out
        assert "[#1]" not in preview.out

        command_show(file="file2.txt")
        selected = capsys.readouterr()
        assert "file2.txt" in selected.out
        assert "[#1]" in selected.out

        cached = load_line_changes_from_state()
        assert cached is not None
        assert cached.path == "file2.txt"
