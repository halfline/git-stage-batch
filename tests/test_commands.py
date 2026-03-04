"""Tests for command implementations."""

import subprocess
from unittest.mock import patch

import pytest

from git_stage_batch.commands import (
    command_again,
    command_block_file,
    command_discard,
    command_discard_line,
    command_include,
    command_include_file,
    command_include_line,
    command_show,
    command_skip,
    command_skip_file,
    command_skip_line,
    command_start,
    command_status,
    command_stop,
    command_unblock_file,
)
from git_stage_batch.state import (
    get_current_hunk_patch_file_path,
    get_state_directory_path,
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

    # Create initial commit with a file
    (repo / "test.txt").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCommandStart:
    """Tests for command_start."""

    def test_start_with_changes(self, temp_git_repo, capsys):
        """Test starting with pending changes."""
        # Modify the file
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")

        command_start()

        captured = capsys.readouterr()
        assert "test.txt" in captured.out
        assert get_current_hunk_patch_file_path().exists()

    def test_start_without_changes(self, temp_git_repo):
        """Test starting with no changes exits with code 2."""
        # No modifications
        with pytest.raises(SystemExit) as exc_info:
            command_start()
        assert exc_info.value.code == 2

    def test_start_clears_previous_state(self, temp_git_repo, capsys):
        """Test that start clears previous hunk state."""
        # Create changes and start
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        # Verify state exists
        assert get_current_hunk_patch_file_path().exists()

        # Start again should clear and re-find
        command_start()
        assert get_current_hunk_patch_file_path().exists()


class TestCommandShow:
    """Tests for command_show."""

    def test_show_current_hunk(self, temp_git_repo, capsys):
        """Test showing the current hunk."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        capsys.readouterr()  # Clear start output
        command_show()

        captured = capsys.readouterr()
        assert "test.txt" in captured.out

    def test_show_without_start_errors(self, temp_git_repo):
        """Test show without start fails."""
        with pytest.raises(SystemExit):
            command_show()

    def test_show_porcelain_with_hunk(self, temp_git_repo):
        """Test show --porcelain exits 0 when hunk exists."""
        import subprocess
        import sys

        (temp_git_repo / "test.txt").write_text("modified\n")
        subprocess.run([sys.executable, "-m", "git_stage_batch.cli", "start"],
                       check=True, cwd=temp_git_repo, capture_output=True)

        result = subprocess.run(
            [sys.executable, "-m", "git_stage_batch.cli", "show", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )

        assert result.returncode == 0
        assert result.stdout == ""  # No output in porcelain mode

    def test_show_porcelain_without_hunk(self, temp_git_repo):
        """Test show --porcelain exits 1 when no hunk exists."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "git_stage_batch.cli", "show", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )

        assert result.returncode == 1
        assert result.stdout == ""  # No output in porcelain mode


class TestCommandInclude:
    """Tests for command_include."""

    def test_include_stages_hunk(self, temp_git_repo):
        """Test including a hunk stages it to the index."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()
        command_include()

        # Check that change is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in result.stdout

    def test_include_advances_to_next_hunk(self, temp_git_repo, capsys):
        """Test that include advances to next hunk if available."""
        # Create two separate changes
        (temp_git_repo / "test.txt").write_text("modified1\nline2\nline3\n")
        (temp_git_repo / "test2.txt").write_text("new file\n")

        command_start()
        capsys.readouterr()

        command_include()

        # Should show next hunk or "No pending hunks"
        captured = capsys.readouterr()
        assert "test2.txt" in captured.out or "No pending hunks" in captured.err


class TestCommandSkip:
    """Tests for command_skip."""

    def test_skip_skips_hunk(self, temp_git_repo, capsys):
        """Test skipping a hunk skips it."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        capsys.readouterr()
        command_skip()

        # Check that nothing is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == ""


class TestCommandDiscard:
    """Tests for command_discard."""

    def test_discard_removes_from_working_tree(self, temp_git_repo):
        """Test discarding removes changes from working tree."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()
        command_discard()

        # Check that working tree is restored
        content = (temp_git_repo / "test.txt").read_text()
        assert content == "line1\nline2\nline3\n"


class TestCommandIncludeLine:
    """Tests for command_include_line."""

    def test_include_line_stages_specific_lines(self, temp_git_repo):
        """Test including specific lines."""
        (temp_git_repo / "test.txt").write_text("modified1\nmodified2\nline3\n")
        command_start()

        # The hunk has deletions (IDs 1,2) and additions (IDs 3,4)
        # Include the deletion of line1 and addition of modified1
        command_include_line("1,3")

        # Check staged content has only first change
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified1" in result.stdout
        # Second line should still be original
        assert "line2" in result.stdout

    def test_include_line_with_range(self, temp_git_repo):
        """Test including a range of lines."""
        (temp_git_repo / "test.txt").write_text("mod1\nmod2\nmod3\n")
        command_start()

        # Include all changes (deletions 1-3, additions 4-6)
        command_include_line("1-6")

        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "mod1" in result.stdout
        assert "mod2" in result.stdout
        assert "mod3" in result.stdout


class TestCommandSkipLine:
    """Tests for command_skip_line."""

    def test_skip_line_marks_as_skipped(self, temp_git_repo, capsys):
        """Test skipping specific lines."""
        (temp_git_repo / "test.txt").write_text("mod1\nmod2\nline3\n")
        command_start()

        capsys.readouterr()
        command_skip_line("1")

        # Should still show hunk with remaining lines
        captured = capsys.readouterr()
        assert "[#2]" in captured.out


class TestCommandDiscardLine:
    """Tests for command_discard_line."""

    def test_discard_line_removes_from_working_tree(self, temp_git_repo):
        """Test discarding specific lines."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        # Discard the modification (line ID 2 is the + line)
        command_discard_line("2")

        # Check working tree
        content = (temp_git_repo / "test.txt").read_text()
        assert "modified" not in content


class TestCommandAgain:
    """Tests for command_again."""

    def test_again_clears_and_restarts(self, temp_git_repo, capsys):
        """Test that again clears state and starts fresh."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        # Skip the hunk
        command_skip()

        capsys.readouterr()
        # Run again - should show the same hunk again
        command_again()

        captured = capsys.readouterr()
        assert "test.txt" in captured.out


class TestCommandStop:
    """Tests for command_stop."""

    def test_stop_clears_state(self, temp_git_repo, capsys):
        """Test that stop clears all state."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        command_stop()

        captured = capsys.readouterr()
        assert "State cleared" in captured.out
        assert not get_state_directory_path().exists()


class TestCommandStatus:
    """Tests for command_status."""

    def test_status_with_current_hunk(self, temp_git_repo, capsys):
        """Test status shows current hunk info."""
        (temp_git_repo / "test.txt").write_text("line1\nmodified\nline3\n")
        command_start()

        capsys.readouterr()
        command_status()

        captured = capsys.readouterr()
        assert "current:" in captured.out
        assert "test.txt" in captured.out
        assert "remaining lines:" in captured.out

    def test_status_without_current_hunk(self, temp_git_repo, capsys):
        """Test status without a current hunk."""
        command_status()

        captured = capsys.readouterr()
        assert "current: none" in captured.out
        assert "blocked: 0" in captured.out

    def test_status_porcelain_output(self, temp_git_repo):
        """Test status with --porcelain flag outputs JSON."""
        import subprocess
        import sys
        import json

        # Make a change and start
        (temp_git_repo / "test.txt").write_text("modified\n")
        subprocess.run([sys.executable, "-m", "git_stage_batch.cli", "start"],
                       check=True, cwd=temp_git_repo, capture_output=True)

        # Run status with --porcelain
        result = subprocess.run(
            [sys.executable, "-m", "git_stage_batch.cli", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )

        assert result.returncode == 0

        # Parse JSON output
        status_data = json.loads(result.stdout)

        # Verify structure
        assert "current_hunk" in status_data
        assert "remaining_line_ids" in status_data
        assert "blocked_hunks" in status_data
        assert "state_directory" in status_data

        # Verify values
        assert status_data["current_hunk"] is not None
        assert "test.txt" in status_data["current_hunk"]
        assert isinstance(status_data["remaining_line_ids"], list)
        assert len(status_data["remaining_line_ids"]) > 0
        assert status_data["blocked_hunks"] == 0


class TestIntegrationWorkflow:
    """Integration tests for complete workflows."""

    def test_partial_staging_workflow(self, temp_git_repo):
        """Test a complete workflow of partial staging."""
        # Create multiple changes
        (temp_git_repo / "test.txt").write_text("mod1\nmod2\nmod3\n")

        # Start and include first two changes
        # IDs 1,2,3 are deletions, IDs 4,5,6 are additions
        command_start()
        command_include_line("1,4")  # First change: delete line1, add mod1

        command_include_line("2,5")  # Second change: delete line2, add mod2

        # Skip remaining lines (completes the hunk)
        command_skip_line("3,6")

        # Verify staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "mod1" in result.stdout
        assert "mod2" in result.stdout
        assert "line3" in result.stdout  # Third line unchanged

    def test_mixed_include_and_discard(self, temp_git_repo):
        """Test mixing include and discard operations."""
        (temp_git_repo / "test.txt").write_text("keep\ndiscard\nline3\n")

        command_start()

        # IDs 1,2 are deletions (line1, line2), IDs 3,4 are additions (keep, discard)
        # Include first change (delete line1, add keep)
        command_include_line("1,3")

        # Discard second change (the addition of "discard")
        command_discard_line("4")

        # Check staged
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "keep" in result.stdout
        assert "line2" in result.stdout  # line2 kept (deletion ID 2 not included)

        # Check working tree - "discard" removed, but line2 wasn't restored
        # (we only discarded the addition, not the deletion)
        content = (temp_git_repo / "test.txt").read_text()
        assert "discard" not in content
        assert "keep" in content
        assert "line3" in content
class TestAutoAddUntrackedFiles:
    """Tests for auto-adding untracked files."""

    def test_auto_add_untracked_file_on_start(self, temp_git_repo, capsys):
        """Test that untracked files are auto-added when starting."""
        # Create an untracked file
        (temp_git_repo / "new_file.txt").write_text("new content\n")

        # Start should auto-add the file
        command_start()

        # Verify file was added with -N (intent to add)
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "new_file.txt" in result.stdout

        # Verify it appears in the diff
        captured = capsys.readouterr()
        assert "new_file.txt" in captured.out

    def test_auto_add_respects_gitignore(self, temp_git_repo, capsys):
        """Test that auto-add respects .gitignore patterns."""
        # Create .gitignore
        (temp_git_repo / ".gitignore").write_text("*.log\n")

        # Create files - one tracked, one ignored
        (temp_git_repo / "new_file.txt").write_text("should appear\n")
        (temp_git_repo / "debug.log").write_text("should not appear\n")

        command_start()

        # Verify .log file was not auto-added
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "new_file.txt" in result.stdout
        assert "debug.log" not in result.stdout

    def test_auto_add_multiple_untracked_files(self, temp_git_repo, capsys):
        """Test auto-adding multiple untracked files."""
        # Create multiple untracked files
        (temp_git_repo / "file1.txt").write_text("content1\n")
        (temp_git_repo / "file2.txt").write_text("content2\n")
        (temp_git_repo / "file3.txt").write_text("content3\n")

        command_start()

        # All should be added
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" in result.stdout
        assert "file3.txt" in result.stdout

    def test_auto_add_idempotent(self, temp_git_repo):
        """Test that auto-add doesn't re-add already added files within same session."""
        from git_stage_batch.state import (
            get_auto_added_files_file_path,
            read_file_paths_file,
        )

        # Create untracked file
        (temp_git_repo / "new_file.txt").write_text("content\n")

        # First start
        command_start()

        # Check auto-added file list
        auto_added_1 = read_file_paths_file(get_auto_added_files_file_path())
        assert "new_file.txt" in auto_added_1

        # Create a new untracked file and call start again without clearing state
        (temp_git_repo / "another_file.txt").write_text("more content\n")

        # Manually call the function that would be triggered on next hunk advancement
        from git_stage_batch.commands import auto_add_untracked_files
        auto_add_untracked_files()

        # Should have both files now, with new_file.txt not duplicated
        auto_added_2 = read_file_paths_file(get_auto_added_files_file_path())
        assert "new_file.txt" in auto_added_2
        assert "another_file.txt" in auto_added_2
        # List should be sorted and deduplicated (only 2 items total)
        assert len(auto_added_2) == 2

class TestBlockFileCommand:
    """Tests for block-file command."""

    def test_block_file_adds_to_gitignore(self, temp_git_repo, capsys):
        """Test that block-file adds file to .gitignore with marker."""
        from git_stage_batch.state import get_gitignore_path

        # Create untracked file
        (temp_git_repo / "unwanted.txt").write_text("ignore me\n")
        command_start()

        # Block the file
        command_block_file("unwanted.txt")

        # Check .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "unwanted.txt" in content
        assert "# git-stage-batch: blocked" in content

        captured = capsys.readouterr()
        assert "Blocked file: unwanted.txt" in captured.err

    def test_block_file_without_argument_uses_current_hunk(self, temp_git_repo, capsys):
        """Test that block-file without argument blocks current hunk's file."""
        from git_stage_batch.state import (
            get_blocked_files_file_path,
            get_gitignore_path,
            read_file_paths_file,
        )

        # Create untracked file
        (temp_git_repo / "current.txt").write_text("content\n")
        command_start()

        # Block current hunk's file (no argument)
        command_block_file("")

        # Check it was blocked
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "current.txt" in blocked

        gitignore = get_gitignore_path()
        assert "current.txt" in gitignore.read_text()

    def test_block_file_resets_auto_added(self, temp_git_repo):
        """Test that blocking a file resets it if it was auto-added."""
        from git_stage_batch.state import (
            get_auto_added_files_file_path,
            read_file_paths_file,
        )

        # Create untracked file
        (temp_git_repo / "temp.txt").write_text("temporary\n")
        command_start()

        # Verify it was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "temp.txt" in auto_added

        # Block it
        command_block_file("temp.txt")

        # Should be removed from auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "temp.txt" not in auto_added

        # File should no longer appear in git ls-files
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "temp.txt" not in result.stdout

    def test_block_file_advances_to_next_hunk(self, temp_git_repo, capsys):
        """Test that blocking current file advances to next hunk."""
        # Create two untracked files
        (temp_git_repo / "file1.txt").write_text("first\n")
        (temp_git_repo / "file2.txt").write_text("second\n")

        command_start()
        captured = capsys.readouterr()

        # Should show one of the files
        assert "file1.txt" in captured.out or "file2.txt" in captured.out

        # Block current file
        command_block_file("")

        captured = capsys.readouterr()
        # Should advance to another file or show "No pending hunks"
        assert "file1.txt" in captured.out or "file2.txt" in captured.out or "No pending hunks" in captured.err

    def test_block_file_respects_blocked_list(self, temp_git_repo):
        """Test that blocked files are in blocked list and .gitignore."""
        from git_stage_batch.state import (
            get_blocked_files_file_path,
            get_gitignore_path,
            read_file_paths_file,
        )

        # Create untracked file
        (temp_git_repo / "blocked.txt").write_text("blocked content\n")
        command_start()

        # Block it
        command_block_file("blocked.txt")

        # Verify it's in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "blocked.txt" in blocked

        # Verify it's in .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "blocked.txt\n" in content
        assert "# git-stage-batch: blocked" in content


class TestUnblockFileCommand:
    """Tests for unblock-file command."""

    def test_unblock_file_removes_from_gitignore(self, temp_git_repo, capsys):
        """Test that unblock-file removes file from .gitignore."""
        from git_stage_batch.state import get_gitignore_path

        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_start()
        command_block_file("temp.txt")

        # Verify it's in .gitignore
        gitignore = get_gitignore_path()
        assert "temp.txt" in gitignore.read_text()

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from .gitignore
        content = gitignore.read_text()
        assert "temp.txt" not in content

        captured = capsys.readouterr()
        assert "Unblocked file: temp.txt" in captured.err

    def test_unblock_file_removes_from_blocked_list(self, temp_git_repo):
        """Test that unblock-file removes from blocked list."""
        from git_stage_batch.state import (
            get_blocked_files_file_path,
            read_file_paths_file,
        )

        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_start()
        command_block_file("temp.txt")

        # Verify it's in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" in blocked

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" not in blocked

    def test_unblock_file_requires_argument(self, temp_git_repo):
        """Test that unblock-file requires a file path argument."""
        import pytest

        with pytest.raises(SystemExit):
            command_unblock_file("")

    def test_unblock_file_makes_file_available_again(self, temp_git_repo):
        """Test that unblocked file is removed from blocked list."""
        from git_stage_batch.state import (
            get_blocked_files_file_path,
            get_gitignore_path,
            read_file_paths_file,
        )

        # Create and block a file
        (temp_git_repo / "temp.txt").write_text("content\n")
        command_start()
        command_block_file("temp.txt")

        # Verify it's blocked
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" in blocked
        assert "temp.txt" in get_gitignore_path().read_text()

        # Unblock it
        command_unblock_file("temp.txt")

        # Should be removed from blocked list and .gitignore
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "temp.txt" not in blocked
        assert "temp.txt" not in get_gitignore_path().read_text()

    def test_unblock_preserves_manual_gitignore_entries(self, temp_git_repo, capsys):
        """Test that unblock doesn't remove manual .gitignore entries."""
        from git_stage_batch.state import get_gitignore_path

        # Create .gitignore with manual entry
        gitignore = get_gitignore_path()
        gitignore.write_text("manual_entry.txt\n")

        # Try to unblock it (shouldn't remove it)
        command_unblock_file("manual_entry.txt")

        # Manual entry should still be there
        content = gitignore.read_text()
        assert "manual_entry.txt" in content

        captured = capsys.readouterr()
        assert "was not in .gitignore with our marker" in captured.err


class TestCleanupAutoAddedFiles:
    """Tests for cleaning up auto-added files on stop/again."""

    def test_stop_resets_auto_added_files(self, temp_git_repo):
        """Test that stop resets auto-added files to untracked."""
        # Create untracked file
        (temp_git_repo / "temp.txt").write_text("content\n")

        # Start - auto-adds the file
        command_start()

        # Verify it was auto-added (appears in git ls-files)
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "temp.txt" in result.stdout

        # Stop should reset it
        command_stop()

        # File should no longer be in index (not in git ls-files)
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "temp.txt" not in result.stdout

        # But file should still exist on disk
        assert (temp_git_repo / "temp.txt").exists()

    def test_again_resets_auto_added_files(self, temp_git_repo, capsys):
        """Test that again resets auto-added files before restarting."""
        # Create untracked file
        (temp_git_repo / "temp.txt").write_text("content\n")

        # Start - auto-adds the file
        command_start()

        # Verify it was auto-added
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "temp.txt" in result.stdout

        # Again should reset and re-add
        command_again()

        # File should still be in index (re-added by again)
        result = subprocess.run(
            ["git", "ls-files", "-t"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "temp.txt" in result.stdout

    def test_stop_handles_missing_auto_added_file(self, temp_git_repo):
        """Test that stop handles case where auto-added file was deleted."""
        # Create untracked file
        (temp_git_repo / "temp.txt").write_text("content\n")

        # Start - auto-adds the file
        command_start()

        # Delete the file from disk
        (temp_git_repo / "temp.txt").unlink()

        # Stop should not error even though file is gone
        command_stop()  # Should not raise


class TestIncludeFileCommand:
    """Tests for include-file command."""

    def test_include_file_stages_all_hunks_in_file(self, temp_git_repo):
        """Test that include-file stages all changes in the current file."""
        # Create a file with multiple separate changes (multiple hunks)
        (temp_git_repo / "multi.txt").write_text("line1\nline2\nline3\nline4\nline5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes that will create multiple hunks (separated by context)
        (temp_git_repo / "multi.txt").write_text("mod1\nline2\nline3\nline4\nmod5\n")

        # Start
        command_start()

        # Include entire file
        command_include_file()

        # Check staged content - both changes should be staged
        result = subprocess.run(
            ["git", "show", ":multi.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "mod1" in result.stdout
        assert "mod5" in result.stdout

    def test_include_file_advances_to_next_file(self, temp_git_repo, capsys):
        """Test that include-file advances to the next file."""
        # Create two files with changes
        (temp_git_repo / "file1.txt").write_text("change1\n")
        (temp_git_repo / "file2.txt").write_text("change2\n")

        # Start
        command_start()

        # Note which file is current
        captured = capsys.readouterr()
        first_file = "file1.txt" if "file1.txt" in captured.out else "file2.txt"
        second_file = "file2.txt" if first_file == "file1.txt" else "file1.txt"

        # Include entire first file
        command_include_file()

        # Should advance to second file
        captured = capsys.readouterr()
        assert second_file in captured.out

    def test_include_file_with_single_hunk(self, temp_git_repo):
        """Test include-file works with a file that has only one hunk."""
        # Create file with single change
        (temp_git_repo / "single.txt").write_text("line1\nline2\n")
        subprocess.run(["git", "add", "single.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add single.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "single.txt").write_text("modified\nline2\n")

        command_start()
        command_include_file()

        # Check staged
        result = subprocess.run(
            ["git", "show", ":single.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "modified" in result.stdout

    def test_include_file_error_without_current_hunk(self, temp_git_repo):
        """Test that include-file errors when no current hunk."""
        import pytest

        with pytest.raises(SystemExit):
            command_include_file()


class TestSkipFileCommand:
    """Tests for skip-file command."""

    def test_skip_file_skips_all_hunks_in_file(self, temp_git_repo):
        """Test that skip-file skips all changes in the current file."""
        # Create a file with multiple hunks
        (temp_git_repo / "multi.txt").write_text("line1\nline2\nline3\nline4\nline5\n")
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "multi.txt").write_text("mod1\nline2\nline3\nline4\nmod5\n")

        command_start()
        command_skip_file()

        # Check nothing staged from multi.txt
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "multi.txt" not in result.stdout

    def test_skip_file_advances_to_next_file(self, temp_git_repo, capsys):
        """Test that skip-file advances to the next file."""
        # Create two files
        (temp_git_repo / "file1.txt").write_text("change1\n")
        (temp_git_repo / "file2.txt").write_text("change2\n")

        command_start()

        captured = capsys.readouterr()
        first_file = "file1.txt" if "file1.txt" in captured.out else "file2.txt"
        second_file = "file2.txt" if first_file == "file1.txt" else "file1.txt"

        # Skip entire first file
        command_skip_file()

        # Should advance to second file
        captured = capsys.readouterr()
        assert second_file in captured.out

    def test_skip_file_error_without_current_hunk(self, temp_git_repo):
        """Test that skip-file errors when no current hunk."""
        import pytest

        with pytest.raises(SystemExit):
            command_skip_file()


class TestCLIDefaultBehavior:
    """Tests for CLI default command behavior."""

    def test_no_command_with_no_session(self, temp_git_repo):
        """Test that no command with no session shows helpful message."""
        import subprocess
        import sys
        
        # Run git-stage-batch with no command
        result = subprocess.run(
            [sys.executable, "-m", "git_stage_batch.cli"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )
        
        assert result.returncode == 1
        assert "No batch staging session in progress" in result.stderr
        assert "git-stage-batch start" in result.stderr

    def test_no_command_with_active_session(self, temp_git_repo):
        """Test that no command with active session defaults to include."""
        import subprocess
        import sys
        
        # Make a change
        (temp_git_repo / "test.txt").write_text("modified\n")
        
        # Start session
        subprocess.run([sys.executable, "-m", "git_stage_batch.cli", "start"], 
                       check=True, cwd=temp_git_repo, capture_output=True)
        
        # Run with no command (should include)
        result = subprocess.run(
            [sys.executable, "-m", "git_stage_batch.cli"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )
        
        assert result.returncode == 0
        
        # Check that content was staged
        staged = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            cwd=temp_git_repo
        )
        assert "modified" in staged.stdout

