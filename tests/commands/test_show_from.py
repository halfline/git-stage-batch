"""Tests for show from batch command."""

from git_stage_batch.commands.start import command_start
from git_stage_batch.commands.include import command_include_to_batch
from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.batch.storage import add_file_to_batch
from git_stage_batch.batch.ownership import DeletionClaim
from git_stage_batch.data.hunk_tracking import render_batch_file_display
import git_stage_batch.batch.merge as merge_module
import git_stage_batch.batch.display as display_module
import git_stage_batch.data.hunk_tracking as hunk_tracking

import subprocess

import pytest

from git_stage_batch.batch import create_batch
from git_stage_batch.commands.show_from import command_show_from_batch
from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.exceptions import CommandError
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


class TestCommandShowFromBatch:
    """Tests for show from batch command."""

    def test_show_from_batch_displays_changes(self, temp_git_repo, capsys):
        """Test showing changes from a batch."""

        # Create a new file and save to batch
        (temp_git_repo / "file.txt").write_text("content\n")
        command_start()
        command_include_to_batch("test-batch", quiet=True)

        command_show_from_batch("test-batch")

        captured = capsys.readouterr()
        assert "file.txt" in captured.out
        assert "content" in captured.out
        assert "[#1]" in captured.out  # Check for line ID annotation

    def test_show_from_empty_batch_succeeds(self, temp_git_repo):
        """Test showing from an empty batch succeeds with no output."""
        create_batch("empty-batch")
        # Empty batch (only contains baseline from HEAD) has no diff

        # Empty batch should succeed but produce no output
        command_show_from_batch("empty-batch")

    def test_show_from_nonexistent_batch_fails(self, temp_git_repo):
        """Test showing from nonexistent batch fails."""
        with pytest.raises(CommandError):
            command_show_from_batch("nonexistent")

    def test_show_from_batch_outside_repo_fails(self, tmp_path, monkeypatch):
        """Test showing from batch outside repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        with pytest.raises(CommandError):
            command_show_from_batch("test-batch")

    def test_show_line_level_requires_single_file_context(self, temp_git_repo):
        """Test that line-level filtering without file context errors out."""

        # Create two files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify both files
        (temp_git_repo / "file1.txt").write_text("line 1\nnew line\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nnew line\nline B\n")

        # Save both files to batch
        command_start()
        command_include_to_batch("multi-file-batch", quiet=True, file="file1.txt")
        command_include_to_batch("multi-file-batch", quiet=True, file="file2.txt")

        # Try line-level show without file context - should fail
        with pytest.raises(CommandError, match="Line-level.*requires single-file context"):
            command_show_from_batch("multi-file-batch", line_ids="1")

    def test_show_line_level_with_file_context_succeeds(self, temp_git_repo, capsys):
        """Test that line-level filtering with --file context succeeds."""

        # Create file
        (temp_git_repo / "file.txt").write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Modify file
        (temp_git_repo / "file.txt").write_text("line 1\nnew line\nline 2\nline 3\n")

        # Save to batch
        command_start()
        command_include_to_batch("single-file-batch", quiet=True)

        # Show with line filtering and file context
        command_show_from_batch("single-file-batch", line_ids="1", file="file.txt")

        captured = capsys.readouterr()
        # Should show filtered content
        assert "file.txt" in captured.out

    def test_show_all_files_without_line_filtering_succeeds(self, temp_git_repo, capsys):
        """Test showing all files without line filtering works."""

        # Create two files
        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create batch
        create_batch("multi-file-batch")

        # Modify both files
        (temp_git_repo / "file1.txt").write_text("line 1\nnew line\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nnew line\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Modified"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add both files to batch
        ownership1 = BatchOwnership(claimed_lines=["2"], deletions=[])
        ownership2 = BatchOwnership(claimed_lines=["2"], deletions=[])
        add_file_to_batch("multi-file-batch", "file1.txt", ownership1, "100644")
        add_file_to_batch("multi-file-batch", "file2.txt", ownership2, "100644")

        # Show all files without line filtering - should succeed
        command_show_from_batch("multi-file-batch")

        captured = capsys.readouterr()
        # Should show both files
        assert "file1.txt" in captured.out
        assert "file2.txt" in captured.out

    def test_show_from_probes_mergeability_once_per_ownership_unit(self, temp_git_repo, monkeypatch):
        """A multi-line replacement unit should not be merge-probed once per display line."""

        # Create committed baseline.
        (temp_git_repo / "file.txt").write_text("old one\nold two\nkeep\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        # Current working tree becomes the batch source: one claimed line
        # replaces a two-line deletion block, making a three-display-line unit.
        (temp_git_repo / "file.txt").write_text("new one\nkeep\n")
        create_batch("replacement-batch")
        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[
                DeletionClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"])
            ],
        )
        add_file_to_batch("replacement-batch", "file.txt", ownership, "100644")


        original_merge_batch = merge_module.merge_batch
        calls = []

        def counting_merge_batch(*args, **kwargs):
            calls.append(args)
            return original_merge_batch(*args, **kwargs)

        monkeypatch.setattr(merge_module, "merge_batch", counting_merge_batch)

        rendered = render_batch_file_display("replacement-batch", "file.txt")

        assert rendered is not None
        assert len(rendered.gutter_to_selection_id) == 3
        assert len(calls) == 1

    def test_show_from_builds_display_lines_once_per_render(self, temp_git_repo, monkeypatch):
        """Rendering should reuse display lines for ownership unit grouping."""

        (temp_git_repo / "file.txt").write_text("old one\nold two\nkeep\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "file.txt").write_text("new one\nkeep\n")
        create_batch("display-lines-batch")
        ownership = BatchOwnership(
            claimed_lines=["1"],
            deletions=[
                DeletionClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"])
            ],
        )
        add_file_to_batch("display-lines-batch", "file.txt", ownership, "100644")

        original_build = display_module.build_display_lines_from_batch_source
        calls = []

        def counting_build(*args, **kwargs):
            calls.append(args)
            return original_build(*args, **kwargs)

        monkeypatch.setattr(display_module, "build_display_lines_from_batch_source", counting_build)

        rendered = render_batch_file_display("display-lines-batch", "file.txt")

        assert rendered is not None
        assert len(calls) == 1

    def test_show_from_multi_file_caches_first_render_without_rerendering(self, temp_git_repo, monkeypatch, capsys):
        """Showing all files should not render the first file twice for cache state."""

        (temp_git_repo / "file1.txt").write_text("line 1\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nline B\n")
        subprocess.run(["git", "add", "."], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add files"], check=True, cwd=temp_git_repo, capture_output=True)

        (temp_git_repo / "file1.txt").write_text("line 1\nnew line\nline 2\n")
        (temp_git_repo / "file2.txt").write_text("line A\nnew line\nline B\n")

        command_start()
        command_include_to_batch("multi-file-batch", quiet=True, file="file1.txt")
        command_include_to_batch("multi-file-batch", quiet=True, file="file2.txt")
        capsys.readouterr()

        original_render = hunk_tracking.render_batch_file_display
        rendered_files = []

        def counting_render(batch_name, file_path, metadata=None):
            rendered_files.append(file_path)
            return original_render(batch_name, file_path, metadata=metadata)

        monkeypatch.setattr(hunk_tracking, "render_batch_file_display", counting_render)

        command_show_from_batch("multi-file-batch")

        assert rendered_files == ["file1.txt", "file2.txt"]
