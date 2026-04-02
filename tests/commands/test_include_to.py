"""Tests for include to batch command."""

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
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Verify we have 3 changed lines
        line_changes = load_line_changes_from_state()
        assert len([l for l in line_changes.lines if l.kind != " "]) == 3

        # Include only line 1 to batch
        command_include_to_batch("filter-batch", line_ids="1")

        # Verify filtered hunk now shows only lines 2-3
        filtered_lines = load_line_changes_from_state()
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
        fetch_next_change()

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
        fetch_next_change()

        # Verify filtered hunk shows only line 2 (line 1 was batched)
        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        changed_lines = [l for l in line_changes.lines if l.kind != " "]
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
        fetch_next_change()

        # Include the only changed line to batch
        command_include_to_batch("clear-batch", line_ids="1")

        # Verify hunk is cleared
        line_changes = load_line_changes_from_state()
        assert line_changes is None

        # Verify message printed
        captured = capsys.readouterr()
        assert "No more lines in this hunk" in captured.err
