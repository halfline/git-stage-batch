"""Tests for include to batch command."""

from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.again import command_again
from git_stage_batch.batch import list_batch_files, read_file_from_batch
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.validation import batch_exists
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import NoMoreHunks
from git_stage_batch.utils.paths import ensure_state_directory_exists

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
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


class TestCommandIncludeToBatch:
    """Tests for include to batch command."""

    def test_include_lines_to_batch_filters_hunk(self, temp_git_repo, capsys):
        """Test that batching lines filters hunk to show only remaining lines."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Verify we have 3 changed lines
        line_changes = load_line_changes_from_state()
        assert len([line for line in line_changes.lines if line.kind != " "]) == 3

        # Include only line 1 to batch
        command_include_to_batch("filter-batch", line_ids="1")

        # Verify filtered hunk now shows only lines 2-3
        filtered_lines = load_line_changes_from_state()
        changed_lines = [line for line in filtered_lines.lines if line.kind != " "]
        assert len(changed_lines) == 2
        # Line IDs should be renumbered: was [1,2,3], after filtering [1] should be [1,2]
        assert changed_lines[0].id == 1
        assert changed_lines[1].id == 2

    def test_include_lines_to_batch_displays_filtered_hunk_once(self, temp_git_repo, capsys):
        """Line-level include to batch prints the remaining hunk only once."""

        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        command_start()
        capsys.readouterr()

        command_include_to_batch("filter-batch", line_ids="1")

        captured = capsys.readouterr()
        assert captured.out.count("README.md ::") == 1

    def test_batched_lines_survive_again(self, temp_git_repo):
        """Test that line-level batched lines are filtered out after again.

        Line-level filtering automatically reapplies when loading hunks.
        Batched line IDs are tracked in processed.batch and survive 'again'.
        """

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Include only line 1 to batch
        command_include_to_batch("persist-lines-batch", line_ids="1")

        # Verify batch contains only line 1
        content = read_file_from_batch("persist-lines-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        # Line 2 should not be in batch yet (only line 1 was included)
        lines_in_batch = content.strip().split("\n")
        assert len([line for line in lines_in_batch if line.startswith("Line")]) == 1

        # Run again - line-level filtering should reapply
        command_again()
        fetch_next_change()

        # Verify filtered hunk shows only line 2 (line 1 was batched)
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        changed_lines = [line for line in line_changes.lines if line.kind != " "]
        # Should only show line 2 (line 1 was filtered out as batched)
        assert len(changed_lines) == 1
        assert "Line 2" in changed_lines[0].text

        # Batch still contains the line we saved
        content_after = read_file_from_batch("persist-lines-batch", "README.md")
        assert content_after == content

    def test_filter_clears_hunk_when_all_batched(self, temp_git_repo, capsys):
        """Test that hunk is cleared when all lines are batched."""

        # Modify README with single line
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nSingle line\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Include the only changed line to batch
        command_include_to_batch("clear-batch", line_ids="1")

        # Verify hunk is cleared
        line_changes = load_line_changes_from_state()
        assert line_changes is None

        # Verify message printed
        captured = capsys.readouterr()
        assert "No more lines in this hunk" in captured.err

    def test_include_to_batch_saves_whole_hunk(self, temp_git_repo):
        """Test that include to batch saves changes to batch."""

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified content\n")

        # Start session (needed for batch source commits)
        command_start()
        command_include_to_batch("test-batch")

        # Verify batch was created and contains the file
        files = list_batch_files("test-batch")
        assert "README.md" in files

        # Verify file content was saved to batch
        content = read_file_from_batch("test-batch", "README.md")
        assert content == "# Test\nModified content\n"

        # Verify changes remain unstaged.
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == ""

    def test_include_empty_added_text_file_to_batch(self, temp_git_repo):
        """Whole-file include to batch should persist empty added text files."""
        empty_file = temp_git_repo / "empty.txt"
        empty_file.write_bytes(b"")

        command_start(quiet=True)
        command_include_to_batch("empty-batch", file="empty.txt", quiet=True)

        file_meta = read_batch_metadata("empty-batch")["files"]["empty.txt"]
        assert file_meta["change_type"] == "added"
        assert read_file_from_batch("empty-batch", "empty.txt") == ""
        assert empty_file.exists()

        command_again(quiet=True)
        with pytest.raises(NoMoreHunks):
            fetch_next_change()

    def test_include_empty_deleted_text_file_to_batch(self, temp_git_repo):
        """Whole-file include to batch should persist empty text deletions."""
        empty_file = temp_git_repo / "empty.txt"
        empty_file.write_bytes(b"")
        subprocess.run(["git", "add", "empty.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add empty file"], check=True, cwd=temp_git_repo, capture_output=True)
        empty_file.unlink()

        ensure_state_directory_exists()
        initialize_abort_state()
        command_include_to_batch("empty-delete-batch", file="empty.txt", quiet=True)

        file_meta = read_batch_metadata("empty-delete-batch")["files"]["empty.txt"]
        assert file_meta["change_type"] == "deleted"
        assert read_file_from_batch("empty-delete-batch", "empty.txt") is None
        assert not empty_file.exists()

        command_again(quiet=True)
        with pytest.raises(NoMoreHunks):
            fetch_next_change()

    def test_include_to_batch_auto_creates_batch(self, temp_git_repo):
        """Test that include to batch auto-creates batch if it doesn't exist."""

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Batch doesn't exist yet
        assert not batch_exists("auto-batch")

        # Start session (needed for batch source commits)
        command_start()
        command_include_to_batch("auto-batch")

        # Batch should now exist
        assert batch_exists("auto-batch")

    def test_include_lines_to_batch_saves_partial_content(self, temp_git_repo):
        """Test that line-level batching synthesizes correct partial content."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Include only lines 1-2 to batch
        command_include_to_batch("partial-batch", line_ids="1-2")

        # Verify batch contains partial content (original + lines 1-2)
        content = read_file_from_batch("partial-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        assert "Line 2" in content
        # Should not have Line 3 since we only included lines 1-2
        # (but the base "# Test" should be there)
        assert "# Test" in content

    def test_include_lines_to_batch_accumulates(self, temp_git_repo):
        """Test that multiple line selections accumulate in batch."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Include line 1
        command_include_to_batch("accum-batch", line_ids="1")

        # Include line 3 (line 2 in filtered view)
        command_include_to_batch("accum-batch", line_ids="2")

        # Verify batch contains both lines
        content = read_file_from_batch("accum-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        assert "Line 3" in content

    def test_batched_whole_hunk_survives_again(self, temp_git_repo):
        """Test that whole-hunk batches don't reappear after again command."""

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nBatched content\n")

        # Cache and batch the hunk
        command_start()
        fetch_next_change()
        command_include_to_batch("persist-batch")

        # Run again - should not show batched hunk
        command_again()

        # Verify no hunk is cached (batched hunk filtered out)
        line_changes = load_line_changes_from_state()
        assert line_changes is None

    def test_deletion_lines_are_masked_after_batching(self, temp_git_repo):
        """Test that deletion lines are properly masked after batching."""

        # Create file with 3 lines and commit
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\nLine C\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add lines"], check=True, cwd=temp_git_repo, capture_output=True)

        # Delete middle line
        readme.write_text("# Test\nLine A\nLine C\n")

        # Start session and cache hunk
        command_start()
        fetch_next_change()

        # Batch the deletion (line 1)
        command_include_to_batch("deletion-batch", line_ids="1")

        # Verify hunk is cleared (all lines batched)
        line_changes = load_line_changes_from_state()
        assert line_changes is None

        # Run again - deletion should still be masked
        command_again()

        # Try to find hunk - should raise NoMoreHunks since all lines are masked

        with pytest.raises(NoMoreHunks):
            fetch_next_change()
