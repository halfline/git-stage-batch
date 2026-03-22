"""Tests for line-level staging operations."""

import subprocess

import pytest

from git_stage_batch.core.models import CurrentLines, HunkHeader, LineEntry
from git_stage_batch.staging.operations import (
    build_target_index_content_with_selected_lines,
    build_target_working_tree_content_with_discarded_lines,
    update_index_with_blob_content,
)
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

    return repo


class TestBuildTargetIndexContent:
    """Tests for build_target_index_content_with_selected_lines."""

    def test_include_single_addition(self):
        """Test including a single added line."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "new line"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1}, base_text)

        assert result == "line1\nnew line\nline2\n"

    def test_include_single_deletion(self):
        """Test including a single deleted line."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "deleted line"),
            LineEntry(None, " ", 3, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\ndeleted line\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1}, base_text)

        assert result == "line1\nline2\n"

    def test_skip_addition(self):
        """Test skipping an added line (not including it)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "new line"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, set(), base_text)

        # Not including the addition means base stays the same
        assert result == "line1\nline2\n"

    def test_skip_deletion(self):
        """Test skipping a deleted line (keeping it)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "kept line"),
            LineEntry(None, " ", 3, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nkept line\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, set(), base_text)

        # Not including the deletion means line stays
        assert result == "line1\nkept line\nline2\n"

    def test_include_replacement(self):
        """Test including both deletion and addition (replacement)."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "old line"),
            LineEntry(2, "+", None, 2, "new line"),
            LineEntry(None, " ", 3, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nold line\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1, 2}, base_text)

        assert result == "line1\nnew line\nline2\n"

    def test_partial_selection(self):
        """Test selecting only some changes from a hunk."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, "add1"),
            LineEntry(2, "+", None, 2, "add2"),
            LineEntry(None, " ", 1, 3, "context"),
            LineEntry(3, "+", None, 4, "add3"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "context\n"

        # Include only IDs 1 and 3, skip 2
        result = build_target_index_content_with_selected_lines(current_lines, {1, 3}, base_text)

        assert result == "add1\ncontext\nadd3\n"

    def test_multiple_deletions(self):
        """Test including multiple deletions."""
        header = HunkHeader(1, 4, 1, 1)
        lines = [
            LineEntry(1, "-", 1, None, "delete1"),
            LineEntry(2, "-", 2, None, "delete2"),
            LineEntry(3, "-", 3, None, "delete3"),
            LineEntry(None, " ", 4, 1, "kept"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "delete1\ndelete2\ndelete3\nkept\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1, 2, 3}, base_text)

        assert result == "kept\n"

    def test_hunk_at_beginning_of_file(self):
        """Test hunk starting at line 1."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(1, "+", None, 1, "new first line"),
            LineEntry(None, " ", 1, 2, "line1"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1}, base_text)

        assert result == "new first line\nline1\nline2\n"

    def test_hunk_at_end_of_file(self):
        """Test hunk at the end of a file."""
        header = HunkHeader(2, 1, 2, 2)
        lines = [
            LineEntry(None, " ", 2, 2, "line2"),
            LineEntry(1, "+", None, 3, "new last line"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1}, base_text)

        assert result == "line1\nline2\nnew last line\n"

    def test_empty_base(self):
        """Test with empty base (new file)."""
        header = HunkHeader(0, 0, 1, 2)
        lines = [
            LineEntry(1, "+", None, 1, "line1"),
            LineEntry(2, "+", None, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = ""

        result = build_target_index_content_with_selected_lines(current_lines, {1, 2}, base_text)

        assert result == "line1\nline2\n"

    def test_preserves_trailing_newline(self):
        """Test that trailing newline is preserved from base."""
        header = HunkHeader(1, 1, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\n"

        result = build_target_index_content_with_selected_lines(current_lines, {1}, base_text)

        assert result.endswith("\n")

    def test_empty_include_set(self):
        """Test with empty include set (no changes applied)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "added"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        base_text = "line1\nline2\n"

        result = build_target_index_content_with_selected_lines(current_lines, set(), base_text)

        # No changes should be applied
        assert result == "line1\nline2\n"


class TestBuildTargetWorkingTreeContent:
    """Tests for build_target_working_tree_content_with_discarded_lines."""

    def test_discard_single_addition(self):
        """Test discarding a single added line."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "added line"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nadded line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1}, working_text)

        # Discarding the addition removes it
        assert result == "line1\nline2\n"

    def test_discard_single_deletion(self):
        """Test discarding a deletion (reinserts the line)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "deleted line"),
            LineEntry(None, " ", 3, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"  # Line already deleted in working tree

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1}, working_text)

        # Discarding the deletion reinserts it
        assert result == "line1\ndeleted line\nline2\n"

    def test_keep_addition(self):
        """Test keeping an added line (not discarding)."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "added line"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nadded line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, set(), working_text)

        # Not discarding means working tree stays the same
        assert result == "line1\nadded line\nline2\n"

    def test_keep_deletion(self):
        """Test keeping a deletion (not discarding)."""
        header = HunkHeader(1, 3, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "deleted line"),
            LineEntry(None, " ", 3, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, set(), working_text)

        # Not discarding the deletion means it stays deleted
        assert result == "line1\nline2\n"

    def test_discard_replacement(self):
        """Test discarding a replacement (deletion + addition)."""
        header = HunkHeader(1, 3, 1, 3)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "-", 2, None, "old line"),
            LineEntry(2, "+", None, 2, "new line"),
            LineEntry(None, " ", 3, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nnew line\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1, 2}, working_text)

        # Discarding both reverts to original
        assert result == "line1\nold line\nline2\n"

    def test_partial_discard(self):
        """Test discarding only some changes."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, "add1"),
            LineEntry(2, "+", None, 2, "add2"),
            LineEntry(None, " ", 1, 3, "context"),
            LineEntry(3, "+", None, 4, "add3"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "add1\nadd2\ncontext\nadd3\n"

        # Discard only ID 2
        result = build_target_working_tree_content_with_discarded_lines(current_lines, {2}, working_text)

        assert result == "add1\ncontext\nadd3\n"

    def test_multiple_additions(self):
        """Test discarding multiple additions."""
        header = HunkHeader(1, 1, 1, 4)
        lines = [
            LineEntry(1, "+", None, 1, "add1"),
            LineEntry(2, "+", None, 2, "add2"),
            LineEntry(3, "+", None, 3, "add3"),
            LineEntry(None, " ", 1, 4, "kept"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "add1\nadd2\nadd3\nkept\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1, 2, 3}, working_text)

        assert result == "kept\n"

    def test_hunk_at_beginning(self):
        """Test discarding at beginning of file."""
        header = HunkHeader(1, 2, 1, 3)
        lines = [
            LineEntry(1, "+", None, 1, "added first"),
            LineEntry(None, " ", 1, 2, "line1"),
            LineEntry(None, " ", 2, 3, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "added first\nline1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1}, working_text)

        assert result == "line1\nline2\n"

    def test_preserves_trailing_newline(self):
        """Test that trailing newline is preserved."""
        header = HunkHeader(1, 1, 1, 2)
        lines = [
            LineEntry(None, " ", 1, 1, "line1"),
            LineEntry(1, "+", None, 2, "line2"),
        ]
        current_lines = CurrentLines(path="test.txt", header=header, lines=lines)
        working_text = "line1\nline2\n"

        result = build_target_working_tree_content_with_discarded_lines(current_lines, {1}, working_text)

        assert result.endswith("\n")


class TestUpdateIndexWithBlobContent:
    """Tests for update_index_with_blob_content."""

    def test_update_new_file(self, temp_git_repo):
        """Test updating index with a new file."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "existing.txt").write_text("existing\n")
        subprocess.run(["git", "add", "existing.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Update index with new file
        update_index_with_blob_content("newfile.txt", "new content\n")

        # Verify it's in the index
        result = subprocess.run(
            ["git", "ls-files", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert "newfile.txt" in result.stdout

        # Verify content
        result = subprocess.run(
            ["git", "show", ":newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == "new content\n"

    def test_update_existing_file(self, temp_git_repo):
        """Test updating an existing file in the index."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "file.txt").write_text("original\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Update the index (not working tree)
        update_index_with_blob_content("file.txt", "modified\n")

        # Verify index content changed
        result = subprocess.run(
            ["git", "show", ":file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        assert result.stdout == "modified\n"

        # Verify working tree is unchanged
        assert (temp_git_repo / "file.txt").read_text() == "original\n"

    def test_preserves_file_mode(self, temp_git_repo):
        """Test that file mode is preserved when updating."""
        ensure_state_directory_exists()

        # Create executable file
        (temp_git_repo / "script.sh").write_text("#!/bin/bash\necho hello\n")
        subprocess.run(["git", "add", "script.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "update-index", "--chmod=+x", "script.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Get original mode
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        original_mode = result.stdout.split()[0]

        # Update content
        update_index_with_blob_content("script.sh", "#!/bin/bash\necho goodbye\n")

        # Verify mode is preserved
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "script.sh"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        new_mode = result.stdout.split()[0]
        assert new_mode == original_mode

    def test_defaults_to_regular_file_mode(self, temp_git_repo):
        """Test that new files get regular file mode (100644)."""
        ensure_state_directory_exists()

        # Create initial commit
        (temp_git_repo / "dummy.txt").write_text("dummy\n")
        subprocess.run(["git", "add", "dummy.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=temp_git_repo, capture_output=True)

        # Add new file
        update_index_with_blob_content("newfile.txt", "content\n")

        # Check mode
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", "newfile.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True
        )
        mode = result.stdout.split()[0]
        assert mode == "100644"
