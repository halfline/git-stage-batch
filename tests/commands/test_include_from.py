"""Tests for include from batch command."""

import stat
import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.query import read_batch_metadata
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.commands.discard_from import command_discard_from_batch
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.commands.include_from import command_include_from_batch
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.commands.start import command_start
from git_stage_batch.data.hunk_tracking import render_batch_file_display
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import run_git_command
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

    # Initialize session for batch operations
    ensure_state_directory_exists()
    initialize_abort_state()

    return repo


class TestCommandIncludeFromBatch:
    """Tests for include from batch command."""

    def test_include_from_batch_stages_changes(self, temp_git_repo, capsys):
        """Test including changes from a batch stages them."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("new content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Remove from working tree and unstage
        (temp_git_repo / "new.txt").unlink()
        run_git_command(["reset", "HEAD", "new.txt"], check=False)

        command_include_from_batch("test-batch")

        # Verify file is staged
        result = run_git_command(["diff", "--cached", "--name-only"])
        assert "new.txt" in result.stdout
        assert (temp_git_repo / "new.txt").read_text() == "new content\n"

        captured = capsys.readouterr()
        assert "Staged changes from batch" in captured.err

    def test_include_from_empty_batch_fails(self, temp_git_repo):
        """Test including from an empty batch fails."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        with pytest.raises(CommandError):
            command_include_from_batch("empty-batch")

    def test_include_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test including from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_include_from_batch("nonexistent")

    def test_include_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test including from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_include_from_batch("test-batch")

    def test_include_from_batch_file_mode_filters_to_selected_file(self, temp_git_repo, capsys):
        """Test that --file mode filters batch diff to selected file only."""

        # Create baseline with multiple files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "file1.txt", "file2.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new lines to both files and save to batch
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\nfile1 added\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\nfile2 added\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Reset files to original
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")

        # Show file1 from the batch so --file without PATH reuses that selected file.
        command_show_from_batch("test-batch", file="file1.txt")
        capsys.readouterr()

        command_include_from_batch("test-batch", file="")

        # Verify only file1 is staged (not file2)
        result = run_git_command(["diff", "--cached", "--name-only"])
        staged_files = [f for f in result.stdout.strip().split("\n") if f]
        assert "file1.txt" in staged_files
        assert "file2.txt" not in staged_files

        # Verify file1 has the added line
        result = run_git_command(["show", ":file1.txt"])
        assert "file1 added" in result.stdout
        assert (temp_git_repo / "file1.txt").read_text() == "line 1\nline 2\nfile1 added\n"
        assert (temp_git_repo / "file2.txt").read_text() == "line A\nline B\n"

        captured = capsys.readouterr()
        assert "Staged changes for file1.txt from batch" in captured.err

    def test_include_from_batch_file_mode_requires_cached_hunk(self, temp_git_repo):
        """Test that --file mode requires a cached hunk."""

        # Create a new file and save to batch
        (temp_git_repo / "new.txt").write_text("content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        # Try to use --file without cached hunk (no fetch_next_change call after command_start)
        with pytest.raises(CommandError):
            command_include_from_batch("test-batch", file="")

    def test_include_from_batch_restores_text_executable_mode(self, temp_git_repo):
        """Test whole-file include --from honors the batch target mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        subprocess.run(["git", "checkout", "HEAD", "--", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)

        command_include_from_batch("test-batch", file="tool.sh")

        index_entry = run_git_command(["ls-files", "-s", "--", "tool.sh"]).stdout
        assert index_entry.startswith("100755 ")
        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_apply_from_batch_restores_text_executable_mode(self, temp_git_repo):
        """Test whole-file apply --from honors the batch target mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        subprocess.run(["git", "checkout", "HEAD", "--", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        tool_path.chmod(0o644)

        command_apply_from_batch("test-batch", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_discard_from_batch_restores_text_baseline_executable_mode(self, temp_git_repo):
        """Test whole-file discard --from honors the baseline mode for text files."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o755)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add executable tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho batched\n")
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o644)

        command_discard_from_batch("test-batch", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho base\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_discard_from_batch_removes_added_text_file(self, temp_git_repo):
        """Whole added text files saved to a batch discard back to absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("new content\n")
        command_start()
        command_include_to_batch("test-batch", file="new.txt", quiet=True)

        file_meta = read_batch_metadata("test-batch")["files"]["new.txt"]
        assert file_meta["change_type"] == "added"

        command_discard_from_batch("test-batch", file="new.txt")

        assert not new_path.exists()

    def test_apply_from_batch_removes_deleted_text_file(self, temp_git_repo):
        """Whole deleted text files saved to a batch apply back to absence."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(["git", "add", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gone"], check=True, cwd=temp_git_repo, capture_output=True)

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        file_meta = read_batch_metadata("test-batch")["files"]["gone.txt"]
        assert file_meta["change_type"] == "deleted"

        subprocess.run(["git", "checkout", "HEAD", "--", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        command_apply_from_batch("test-batch", file="gone.txt")

        assert not gone_path.exists()
        assert run_git_command(["diff", "--cached", "--name-only", "--", "gone.txt"]).stdout == ""

    def test_apply_from_batch_line_scoped_deleted_text_file_removes_path(self, temp_git_repo):
        """Selecting every deleted text line should preserve deleted-path semantics."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(["git", "add", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gone"], check=True, cwd=temp_git_repo, capture_output=True)

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(["git", "checkout", "HEAD", "--", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        command_apply_from_batch("test-batch", line_ids="1", file="gone.txt")

        assert not gone_path.exists()
        assert run_git_command(["diff", "--cached", "--name-only", "--", "gone.txt"]).stdout == ""

    def test_apply_from_batch_line_scoped_added_text_file_restores_executable_mode(self, temp_git_repo):
        """Line-scoped apply that creates a text path should use the batch mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.unlink()
        command_apply_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_stages_deleted_text_file_and_removes_worktree_path(self, temp_git_repo):
        """Whole deleted text files saved to a batch include as staged deletions."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(["git", "add", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gone"], check=True, cwd=temp_git_repo, capture_output=True)

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(["git", "checkout", "HEAD", "--", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        command_include_from_batch("test-batch", file="gone.txt")

        assert not gone_path.exists()
        assert run_git_command(["status", "--short", "--", "gone.txt"]).stdout == "D  gone.txt\n"

    def test_include_from_batch_line_scoped_added_text_file_restores_executable_mode(self, temp_git_repo):
        """Line-scoped include that creates a text path should use the batch mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho batched\n")
        tool_path.chmod(0o755)
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        tool_path.unlink()
        run_git_command(["reset", "HEAD", "tool.sh"], check=False)
        command_include_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        index_entry = run_git_command(["ls-files", "-s", "--", "tool.sh"]).stdout
        assert index_entry.startswith("100755 ")
        assert tool_path.read_text() == "#!/bin/sh\necho batched\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_line_scoped_deleted_text_file_stages_deletion(self, temp_git_repo):
        """Line-scoped include of a full deleted text file should stage path deletion."""
        gone_path = temp_git_repo / "gone.txt"
        gone_path.write_text("gone\n")
        subprocess.run(["git", "add", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gone"], check=True, cwd=temp_git_repo, capture_output=True)

        gone_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="gone.txt", quiet=True)

        subprocess.run(["git", "checkout", "HEAD", "--", "gone.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        command_include_from_batch("test-batch", line_ids="1", file="gone.txt")

        assert not gone_path.exists()
        assert run_git_command(["status", "--short", "--", "gone.txt"]).stdout == "D  gone.txt\n"

    def test_include_from_batch_creates_added_empty_text_file(self, temp_git_repo):
        """Empty added text files should not be skipped as empty ownership."""
        empty_path = temp_git_repo / "empty.txt"
        empty_path.write_text("")
        command_start()
        add_file_to_batch("test-batch", "empty.txt", BatchOwnership(claimed_lines=[], deletions=[]))

        empty_path.unlink()
        command_include_from_batch("test-batch", file="empty.txt")

        assert empty_path.exists()
        assert empty_path.read_bytes() == b""
        assert run_git_command(["diff", "--cached", "--name-only", "--", "empty.txt"]).stdout == "empty.txt\n"

    def test_discard_from_batch_line_scoped_added_text_file_removes_path(self, temp_git_repo):
        """Line-scoped discard of a full added text file should restore absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("new\n")
        command_start()
        command_include_to_batch("test-batch", file="new.txt", quiet=True)

        command_discard_from_batch("test-batch", line_ids="1", file="new.txt")

        assert not new_path.exists()

    def test_discard_from_batch_partial_new_text_file_removes_path_when_exhausted(self, temp_git_repo):
        """Discarding the remaining owned content of a partial new file should restore absence."""
        new_path = temp_git_repo / "new.txt"
        new_path.write_text("owned\nunowned\n")
        command_start()
        command_include_to_batch("test-batch", line_ids="1", file="new.txt", quiet=True)

        new_path.write_text("owned\n")

        command_discard_from_batch("test-batch", file="new.txt")

        assert not new_path.exists()

    def test_discard_from_batch_line_scoped_deleted_text_file_restores_executable_mode(self, temp_git_repo):
        """Line-scoped discard that restores a deleted text path should use baseline mode."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o755)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add executable tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.unlink()
        command_start()
        command_include_to_batch("test-batch", file="tool.sh", quiet=True)

        command_discard_from_batch("test-batch", line_ids="1-2", file="tool.sh")

        assert tool_path.read_text() == "#!/bin/sh\necho base\n"
        assert stat.S_IMODE(tool_path.stat().st_mode) & stat.S_IXUSR

    def test_include_from_batch_as_replaces_presence_only_selection(self, temp_git_repo):
        """Test include --from --line --as replaces claimed batch content before staging."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        batch_value_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text == "batch value"
        )

        command_include_from_batch(
            "test-batch",
            line_ids=str(batch_value_gutter),
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_replaces_atomic_replacement_unit(self, temp_git_repo):
        """Test include --from --line --as preserves batch deletion semantics."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("keep\nold value\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("keep\nbatch value\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("keep\nold value\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        replacement_gutters = sorted(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text in {"old value", "batch value"}
        )

        command_include_from_batch(
            "test-batch",
            line_ids=f"{replacement_gutters[0]}-{replacement_gutters[-1]}",
            file="test.txt",
            replacement_text="edited value",
        )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "keep\nedited value\n"
        assert test_file.read_text() == "keep\nedited value\n"

    def test_include_from_batch_as_rejects_partial_replacement_unit(self, temp_git_repo):
        """Test include --from --line --as honors explicit replacement atomicity."""
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("old one\nold two\nkeep\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_text("new one\nnew two\nkeep\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        test_file.write_text("old one\nold two\nkeep\n")
        run_git_command(["reset", "HEAD", "test.txt"], check=False)

        rendered = render_batch_file_display("test-batch", "test.txt")
        new_one_gutter = next(
            rendered.selection_id_to_gutter[line.id]
            for line in rendered.line_changes.lines
            if line.id is not None and line.text == "new one"
        )

        with pytest.raises(CommandError, match="must be selected together"):
            command_include_from_batch(
                "test-batch",
                line_ids=str(new_one_gutter),
                file="test.txt",
                replacement_text="edited value",
            )

        result = run_git_command(["show", ":test.txt"])
        assert result.stdout == "old one\nold two\nkeep\n"
        assert test_file.read_text() == "old one\nold two\nkeep\n"
