"""Tests for include to batch command."""

import subprocess

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import find_and_cache_next_unblocked_hunk
from git_stage_batch.data.line_state import load_current_lines_from_state
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import ensure_state_directory_exists


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
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

        # Verify we have 3 changed lines
        current_lines = load_current_lines_from_state()
        assert len([l for l in current_lines.lines if l.kind != " "]) == 3

        # Include only line 1 to batch
        command_include_to_batch("filter-batch", line_ids="1")

        # Verify filtered hunk now shows only lines 2-3
        filtered_lines = load_current_lines_from_state()
        changed_lines = [l for l in filtered_lines.lines if l.kind != " "]
        assert len(changed_lines) == 2
        # Line IDs should be renumbered: was [1,2,3], after filtering [1] should be [1,2]
        assert changed_lines[0].id == 1
        assert changed_lines[1].id == 2

    def test_batched_lines_survive_again(self, temp_git_repo):
        """Test that line-level batched lines are filtered out after again.

        Line-level filtering automatically reapplies when loading hunks.
        Batched line IDs are tracked in processed.batch and survive 'again'.
        """
        from git_stage_batch.batch import read_file_from_batch
        from git_stage_batch.commands.again import command_again
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

        # Include only line 1 to batch
        command_include_to_batch("persist-lines-batch", line_ids="1")

        # Verify batch contains only line 1
        content = read_file_from_batch("persist-lines-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        # Line 2 should not be in batch yet (only line 1 was included)
        lines_in_batch = content.strip().split("\n")
        assert len([l for l in lines_in_batch if l.startswith("Line")]) == 1

        # Run again - line-level filtering should reapply
        command_again()
        find_and_cache_next_unblocked_hunk()

        # Verify filtered hunk shows only line 2 (line 1 was batched)
        current_lines = load_current_lines_from_state()
        assert current_lines is not None
        changed_lines = [l for l in current_lines.lines if l.kind != " "]
        # Should only show line 2 (line 1 was filtered out as batched)
        assert len(changed_lines) == 1
        assert "Line 2" in changed_lines[0].text

        # Batch still contains the line we saved
        content_after = read_file_from_batch("persist-lines-batch", "README.md")
        assert content_after == content

    def test_filter_clears_hunk_when_all_batched(self, temp_git_repo, capsys):
        """Test that hunk is cleared when all lines are batched."""
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with single line
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nSingle line\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

        # Include the only changed line to batch
        command_include_to_batch("clear-batch", line_ids="1")

        # Verify hunk is cleared
        current_lines = load_current_lines_from_state()
        assert current_lines is None

        # Verify message printed
        captured = capsys.readouterr()
        assert "No more lines in this hunk" in captured.err

    def test_include_to_batch_saves_whole_hunk(self, temp_git_repo):
        """Test that include to batch saves changes to batch."""
        from git_stage_batch.batch import list_batch_files, read_file_from_batch
        from git_stage_batch.commands.include import command_include_to_batch

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

        # Verify changes are NOT staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == ""

    def test_include_to_batch_auto_creates_batch(self, temp_git_repo):
        """Test that include to batch auto-creates batch if it doesn't exist."""
        from git_stage_batch.batch.validation import batch_exists
        from git_stage_batch.commands.include import command_include_to_batch

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
        from git_stage_batch.batch import read_file_from_batch
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

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
        from git_stage_batch.batch import read_file_from_batch
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

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
        from git_stage_batch.commands.again import command_again
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nBatched content\n")

        # Cache and batch the hunk
        command_start()
        find_and_cache_next_unblocked_hunk()
        command_include_to_batch("persist-batch")

        # Run again - should not show batched hunk
        command_again()

        # Verify no hunk is cached (batched hunk filtered out)
        current_lines = load_current_lines_from_state()
        assert current_lines is None

    def test_deletion_lines_are_masked_after_batching(self, temp_git_repo):
        """Test that deletion lines are properly masked after batching."""
        from git_stage_batch.commands.include import command_include_to_batch
        from git_stage_batch.commands.again import command_again
        from git_stage_batch.utils.paths import get_processed_batch_ids_file_path
        import json

        # Create file with 3 lines and commit
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine A\nLine B\nLine C\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add lines"], check=True, cwd=temp_git_repo, capture_output=True)

        # Delete middle line
        readme.write_text("# Test\nLine A\nLine C\n")

        # Start session and cache hunk
        command_start()
        find_and_cache_next_unblocked_hunk()

        # Batch the deletion (line 1)
        command_include_to_batch("deletion-batch", line_ids="1")

        # Verify hunk is cleared (all lines batched)
        current_lines = load_current_lines_from_state()
        assert current_lines is None

        # Verify mask includes deletion_positions
        mask_file = get_processed_batch_ids_file_path()
        assert mask_file.exists()
        mask_data = json.loads(mask_file.read_text())
        assert "README.md" in mask_data
        assert "deletion_positions" in mask_data["README.md"]
        assert "2" in mask_data["README.md"]["deletion_positions"]  # Deletion after line 2

        # Run again - deletion should still be masked
        command_again()
        find_and_cache_next_unblocked_hunk()

        # No hunk should be found (deletion is masked)
        current_lines = load_current_lines_from_state()
        assert current_lines is None
