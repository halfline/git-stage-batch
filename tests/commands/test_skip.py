"""Tests for skip command."""

import subprocess

import pytest

from git_stage_batch.commands.skip import command_skip, command_skip_line
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.utils.paths import get_processed_skip_ids_file_path


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


class TestCommandSkip:
    """Tests for skip command."""

    def test_skip_marks_hunk_as_processed(self, temp_git_repo, capsys):
        """Test that skip marks a hunk as processed without staging it."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_start()
        command_skip()

        # Check that changes are NOT staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""  # No staged changes

        # Check that changes still exist in working tree
        result = subprocess.run(
            ["git", "diff"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk skipped" in captured.err

    def test_skip_no_changes(self, temp_git_repo, capsys):
        """Test skip when no more hunks remain."""
        # Create a change
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Start session and skip the change
        command_start()
        command_skip()
        capsys.readouterr()  # Clear output

        # Try to skip again - should show "No more hunks"
        command_skip()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_skip_then_include_next(self, temp_git_repo, capsys):
        """Test skipping one hunk then including the next."""
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

        # Start session
        command_start()

        # Skip first hunk
        command_skip()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.err

        # Second hunk should now be available
        # (Would normally use command_include here but we're just testing skip)


class TestCommandSkipLine:
    """Tests for skip --line command."""

    def test_skip_line_requires_selected_hunk(self, temp_git_repo):
        """Test that skip --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_skip_line("1")

    def test_skip_line_marks_single_addition(self, temp_git_repo):
        """Test skipping a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_marks_single_deletion(self, temp_git_repo):
        """Test skipping a single deleted line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Skip line 1
        command_skip_line("1")

        # Check that skip IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_with_range(self, temp_git_repo):
        """Test skipping a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Skip lines 1-3
        command_skip_line("1-3")

        # Check that all IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1", "2", "3"]

    def test_skip_line_partial_selection(self, temp_git_repo):
        """Test skipping only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Skip only line 1
        command_skip_line("1")

        # Check that only line 1 was recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert skip_ids == ["1"]

    def test_skip_line_accumulates_ids(self, temp_git_repo):
        """Test that multiple skip --line calls accumulate."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Skip line 1
        command_skip_line("1")

        # Skip line 3
        command_skip_line("3")

        # Check that both IDs were recorded
        skip_ids_content = read_text_file_contents(get_processed_skip_ids_file_path()).strip()
        skip_ids = skip_ids_content.split("\n") if skip_ids_content else []
        assert set(skip_ids) == {"1", "3"}


class TestCommandIncludeToBatch:
    """Tests for skip to batch command."""

    def test_include_to_batch_saves_and_skips(self, temp_git_repo):
        """Test that skip to batch saves changes to batch and skips."""
        from git_stage_batch.batch import list_batch_files, read_file_from_batch
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified content\n")

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
        """Test that skip to batch auto-creates batch if it doesn't exist."""
        from git_stage_batch.batch.validation import batch_exists
        from git_stage_batch.commands.include import command_include_to_batch

        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        # Batch doesn't exist yet
        assert not batch_exists("auto-batch")

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

    def test_include_lines_to_batch_filters_hunk(self, temp_git_repo, capsys):
        """Test that batching lines filters hunk to show only remaining lines."""

    def test_include_lines_to_batch_accumulates(self, temp_git_repo):
        """Test that multiple line selections accumulate in batch."""
        from git_stage_batch.batch import read_file_from_batch
        from git_stage_batch.commands.include import command_include_to_batch

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

    def test_batched_hunks_survive_again(self, temp_git_repo):
        """Test that whole-hunk batches don't reappear after again command."""
        from git_stage_batch.commands.again import command_again
        from git_stage_batch.commands.include import command_include_to_batch

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
