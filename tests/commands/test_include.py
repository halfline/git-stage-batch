"""Tests for include command."""

import subprocess

import pytest

from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.validation import batch_exists
from git_stage_batch.commands.include import command_include, command_include_line, command_include_line_as, command_include_to_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import fetch_next_change
from git_stage_batch.data.line_state import load_line_changes_from_state
from git_stage_batch.exceptions import CommandError, NoMoreHunks
from git_stage_batch.commands.again import command_again


def _prepare_single_line_change(repo, file_name="test.txt"):
    test_file = repo / file_name
    test_file.write_text("base\n")
    subprocess.run(["git", "add", file_name], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"Add {file_name}"], check=True, cwd=repo, capture_output=True)
    test_file.write_text("base\nselected\n")
    command_start()
    fetch_next_change()
    return test_file


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


class TestCommandInclude:
    """Tests for include command."""

    def test_include_stages_hunk(self, temp_git_repo, capsys):
        """Test that include stages a hunk."""
        # Modify README
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_start()
        command_include()

        # Check that changes are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+New content" in result.stdout

        captured = capsys.readouterr()
        assert "Hunk staged" in captured.err

    def test_include_no_changes(self, temp_git_repo, capsys):
        """Test include when no more hunks remain."""
        # Create a change
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Start session and include the change
        command_start()
        command_include()
        capsys.readouterr()  # Clear output

        # Try to include again - should show "No more hunks"
        command_include()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_include_multiple_hunks(self, temp_git_repo, capsys):
        """Test including multiple hunks sequentially."""
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

        # Include first hunk
        command_include()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.err

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.err

        # Verify both are staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file1.txt" in result.stdout
        assert "file2.txt" in result.stdout

    def test_include_all_hunks_processed(self, temp_git_repo, capsys):
        """Test include when all hunks have been processed."""
        # Modify README before starting
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_start()

        # Include the only hunk
        command_include()
        capsys.readouterr()  # Clear output

        # Try to include again - should say no more hunks
        command_include()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err


class TestCommandIncludeLine:
    """Tests for command_include_line."""

    def test_include_line_requires_selected_hunk(self, temp_git_repo):
        """Test that include --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_include_line("1")

    def test_include_to_batch_line_captures_worktree_executable_mode(self, temp_git_repo):
        """include --to --line should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_include_to_batch("mode-batch", line_ids="1", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_include_to_batch_file_line_captures_worktree_executable_mode(self, temp_git_repo):
        """include --to --file --line should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_include_to_batch("mode-batch", file="tool.sh", line_ids="1", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_include_line_stages_single_addition(self, temp_git_repo):
        """Test including a single added line."""
        # Create a file with content
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\nline2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify the file - add new lines
        test_file.write_text("line1\nnew line\nline2\n")

        command_start()
        fetch_next_change()  # Load the hunk

        # Include only the added line (ID 1)
        command_include_line("1")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new line" in result.stdout
        assert result.stdout == "line1\nnew line\nline2\n"

    def test_include_line_stages_multiple_lines(self, temp_git_repo, capsys):
        """Test including multiple lines."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line1\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add multiple lines
        test_file.write_text("line1\nline2\nline3\nline4\n")

        command_start()
        fetch_next_change()

        # Include lines 1 and 3
        command_include_line("1,3")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "line2" in result.stdout
        assert "line4" in result.stdout
        assert result.stdout == "line1\nline2\nline4\n"

        captured = capsys.readouterr()
        assert "Included line(s): 1,3" in captured.err

    def test_include_line_with_range(self, temp_git_repo):
        """Test including a range of lines."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("a\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add many lines
        test_file.write_text("a\nb\nc\nd\ne\nf\n")

        command_start()
        fetch_next_change()

        # Include lines 1-3
        command_include_line("1-3")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "a\nb\nc\nd\n"

    def test_include_line_incremental(self, temp_git_repo):
        """Test including lines incrementally."""
        # Create a file
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("original\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify file - replace with multiple lines
        test_file.write_text("line1\nline2\nline3\n")

        command_start()
        fetch_next_change()

        # Include line 2 (which adds line1)
        command_include_line("2")

        # Include line 3 (which adds line2) after recalculation
        command_include_line("2")

        # Check staged content
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        # Both lines should be staged
        assert "line1" in result.stdout
        assert "line2" in result.stdout

    def test_include_line_rejects_ids_outside_current_view(self, temp_git_repo):
        """include --line should reject IDs that are not in the current view."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("base\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("base\nselected\n")
        command_start()
        fetch_next_change()

        with pytest.raises(CommandError) as exc_info:
            command_include_line("99")

        assert "Line selection 99 is not valid for test.txt." in exc_info.value.message
        assert "current file view" in exc_info.value.message
        assert "cache" not in exc_info.value.message.lower()
        assert "round" not in exc_info.value.message.lower()
        assert "transient" not in exc_info.value.message.lower()

    def test_include_line_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """include --line should reject the whole selection when any ID is stale."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("base\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("base\nselected\n")
        command_start()
        fetch_next_change()

        with pytest.raises(CommandError) as exc_info:
            command_include_line("1,99")

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert staged.stdout == ""

    def test_include_line_as_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """include --line --as should reject the selection when any ID is stale."""
        _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_include_line_as("1,99", "replacement")

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        staged = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert staged.stdout == ""

    def test_include_to_batch_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """include --to --line should reject the selection when any ID is stale."""
        _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_include_to_batch("invalid-lines", line_ids="1,99", quiet=True)

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert not batch_exists("invalid-lines")

    def test_include_file_to_batch_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """include --to --file --line should reject when any ID is stale."""
        _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_include_to_batch(
                "invalid-lines",
                file="test.txt",
                line_ids="1,99",
                quiet=True,
            )

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert not batch_exists("invalid-lines")

    def test_include_line_handles_deletions(self, temp_git_repo):
        """Test that include --line handles deletions correctly."""
        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep1\nremove\nkeep2\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Remove a line
        test_file.write_text("keep1\nkeep2\n")

        command_start()
        fetch_next_change()

        # Include the deletion (line 1)
        command_include_line("1")

        # Check staged content - line should be removed
        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "remove" not in result.stdout
        assert result.stdout == "keep1\nkeep2\n"

    def test_include_line_as_replaces_staged_content_and_masks_hunk(self, temp_git_repo):
        """Test include --line --as stages replacement text and hides the line."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("old value\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("working value\n")

        command_start()
        fetch_next_change()
        command_include_line_as("1", "staged value")

        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "staged value\n"

        command_again()
        with pytest.raises(NoMoreHunks):
            fetch_next_change()

    def test_include_line_as_replaces_selected_range(self, temp_git_repo):
        """Test include --line --as replaces a contiguous selected range."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("header\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("header\nline1\nline2\nline3\n")

        command_start()
        fetch_next_change()
        command_include_line_as("1-2", "replacement1\nreplacement2")

        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "header\nreplacement1\nreplacement2\n"

    def test_include_line_as_selected_file_recalculates_remaining_lines(self, temp_git_repo):
        """Test include --file --line --as on the selected file recalculates that file."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\nold value\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("keep\nworking value\nextra line\n")

        command_start()
        fetch_next_change()
        command_include_line_as("2", "staged value", file="")

        result = subprocess.run(
            ["git", "show", ":test.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == "keep\nstaged value\n"

        line_changes = load_line_changes_from_state()
        assert line_changes is not None
        changed_lines = [line for line in line_changes.lines if line.kind != " "]
        assert any("extra line" in line.text for line in changed_lines)

    def test_include_line_as_replaces_disjoint_file_scoped_regions(self, temp_git_repo):
        """File-scoped replacement should accept one contiguous range across regions."""
        test_file = temp_git_repo / "multi.txt"
        base_lines = [f"line{i}\n" for i in range(1, 41)]
        test_file.write_text("".join(base_lines))
        subprocess.run(["git", "add", "multi.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add multi file"], check=True, cwd=temp_git_repo, capture_output=True)

        rewritten_lines = (
            base_lines[:5]
            + ["change-one-a\n", "change-one-b\n"]
            + base_lines[5:20]
            + ["change-two-a\n", "change-two-b\n"]
            + base_lines[20:35]
            + ["change-three-a\n", "change-three-b\n"]
            + base_lines[35:]
        )
        test_file.write_text("".join(rewritten_lines))

        command_start()
        staged_span = (
            ["stage-one-a\n", "stage-one-b\n"]
            + base_lines[5:20]
            + ["stage-two-a\n", "stage-two-b\n"]
            + base_lines[20:35]
            + ["stage-three-a\n", "stage-three-b\n"]
        )
        command_include_line_as("1-6", "".join(staged_span), file="multi.txt")

        result = subprocess.run(
            ["git", "show", ":multi.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == (
            "".join(base_lines[:5])
            + "".join(staged_span)
            + "".join(base_lines[35:])
        )
