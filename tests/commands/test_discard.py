"""Tests for discard command."""

import os
from unittest.mock import patch

from git_stage_batch.batch.ownership import BatchOwnership
from git_stage_batch.utils.paths import get_abort_snapshots_directory_path
from git_stage_batch.batch import list_batch_files, read_batch_metadata, read_file_from_batch
from git_stage_batch.commands.discard import command_discard_to_batch
from git_stage_batch.batch.validation import batch_exists
from git_stage_batch.commands.apply_from import command_apply_from_batch
from git_stage_batch.data.hunk_tracking import fetch_next_change, recalculate_selected_hunk_for_file

import subprocess

import pytest

from git_stage_batch.commands.discard import command_discard, command_discard_line, command_discard_line_as_to_batch
from git_stage_batch.commands.include import command_include
from git_stage_batch.commands.start import command_start
from git_stage_batch.exceptions import CommandError


def _prepare_single_line_change(repo, file_name="test.txt"):
    test_file = repo / file_name
    test_file.write_text("base\n")
    subprocess.run(["git", "add", file_name], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"Add {file_name}"], check=True, cwd=repo, capture_output=True)
    test_file.write_text("base\nselected\n")
    command_start()
    fetch_next_change()
    return test_file


def _reject_materialized_ownership_metadata(monkeypatch):
    def fail_from_metadata_dict(cls, data):
        raise AssertionError("discard should use acquired ownership metadata")

    monkeypatch.setattr(
        BatchOwnership,
        "from_metadata_dict",
        classmethod(fail_from_metadata_dict),
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

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestCommandDiscard:
    """Tests for discard command."""

    def test_discard_removes_hunk_from_working_tree(self, temp_git_repo, capsys):
        """Test that discard removes a hunk from the working tree."""
        # Modify README
        readme = temp_git_repo / "README.md"
        original_content = readme.read_text()
        readme.write_text("# Test\nNew content\n")

        command_start()
        command_discard()

        # Changes should be removed from working tree
        assert readme.read_text() == original_content

        # Nothing should be staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""

        captured = capsys.readouterr()
        assert "Hunk discarded" in captured.err

    def test_discard_only_line_from_intent_to_add_file_leaves_empty_file(
        self,
        temp_git_repo,
    ):
        """Discarding the only added line should not leave a blank line."""
        test_file = temp_git_repo / "new.txt"
        test_file.write_bytes(b"added\n")
        subprocess.run(
            ["git", "add", "-N", "new.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        command_start()
        command_discard_line("1")

        assert test_file.read_bytes() == b""
        diff_result = subprocess.run(
            ["git", "diff", "--", "new.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "+added" not in diff_result.stdout

    def test_discard_no_changes(self, temp_git_repo, capsys):
        """Test discard when no more hunks remain."""
        # Create a change
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nModified\n")

        # Start session and discard the change
        command_start()
        command_discard()
        capsys.readouterr()  # Clear output

        # Try to discard again - should show "No more hunks"
        command_discard()

        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_discard_then_include_next(self, temp_git_repo, capsys):
        """Test discarding one hunk then including the next."""
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

        # Start session before discarding
        command_start()

        # Discard first hunk
        command_discard()
        captured = capsys.readouterr()
        assert "file1.txt" in captured.err

        # Verify file1 is restored
        assert file1.read_text() == "original 1\n"

        # Include second hunk
        command_include()
        captured = capsys.readouterr()
        assert "file2.txt" in captured.err

        # Verify only file2 is staged
        result = subprocess.run(
            ["git", "diff", "--cached"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "file2.txt" in result.stdout
        assert "file1.txt" not in result.stdout

    def test_discard_all_hunks_processed(self, temp_git_repo, capsys):
        """Test discard when all hunks have been processed."""
        # Modify README before starting
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew content\n")

        command_start()

        # Discard the only hunk
        command_discard()
        capsys.readouterr()  # Clear output

        # Try to discard again
        command_discard()
        captured = capsys.readouterr()
        assert "No more hunks to process" in captured.err

    def test_discard_snapshots_untracked_file(self, temp_git_repo):
        """Test that discard snapshots content of untracked files."""

        # Create an untracked file
        untracked_file = temp_git_repo / "untracked.txt"
        original_content = "untracked content\n"
        untracked_file.write_text(original_content)

        # Auto-add with -N to make it visible to diff
        subprocess.run(["git", "add", "-N", "untracked.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Start session (initializes abort state)
        command_start()

        # Discard the file (should create snapshot before discarding)
        command_discard()

        # Verify snapshot was created
        snapshot_dir = get_abort_snapshots_directory_path()
        snapshot_file = snapshot_dir / "untracked.txt"
        assert snapshot_file.exists()
        assert snapshot_file.read_text() == original_content


class TestCommandDiscardLine:
    """Tests for discard --line command."""

    def test_discard_all_changes_restores_missing_final_newline(
        self,
        temp_git_repo,
    ):
        """Discard should keep a missing final newline from the old content."""
        test_file = temp_git_repo / "f.txt"
        original = b"a\naa"
        modified = b"b\nc\na\nb\n"
        test_file.write_bytes(original)
        subprocess.run(["git", "add", "f.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add f"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_bytes(modified)
        command_start(quiet=True)
        command_discard_line("1,2,3,4", file="f.txt")

        assert test_file.read_bytes() == original

    def test_discard_all_changes_restores_present_final_newline(
        self,
        temp_git_repo,
    ):
        """Discard should keep a present final newline from the old content."""
        test_file = temp_git_repo / "f.txt"
        original = b"a\naa\n"
        modified = b"b\nc\na\nb"
        test_file.write_bytes(original)
        subprocess.run(["git", "add", "f.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add f"], check=True, cwd=temp_git_repo, capture_output=True)

        test_file.write_bytes(modified)
        command_start(quiet=True)
        command_discard_line("1,2,3,4", file="f.txt")

        assert test_file.read_bytes() == original

    def test_discard_line_symlink_restores_link_without_touching_referent(
        self,
        temp_git_repo,
    ):
        """Discarding a symlink target change should rewrite the link itself."""
        link_path = temp_git_repo / "link"
        target_path = temp_git_repo / "newtarget"
        os.symlink("oldtarget", link_path)
        subprocess.run(["git", "add", "link"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add link"], check=True, cwd=temp_git_repo, capture_output=True)
        link_path.unlink()
        os.symlink("newtarget", link_path)
        target_path.write_bytes(b"contents of target\n")

        command_start(quiet=True)
        command_discard_line("1,2")

        assert os.readlink(link_path) == "oldtarget"
        assert target_path.read_bytes() == b"contents of target\n"

    def test_discard_line_requires_selected_hunk(self, temp_git_repo):
        """Test that discard --line requires an active hunk."""
        with pytest.raises(CommandError):
            command_discard_line("1")

    def test_discard_to_batch_line_captures_worktree_executable_mode(self, temp_git_repo):
        """discard --to --line should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_discard_to_batch("mode-batch", line_ids="1", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_discard_to_batch_line_as_captures_worktree_executable_mode(self, temp_git_repo):
        """discard --to --line --as should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_discard_line_as_to_batch("mode-batch", "1", "replacement", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_discard_to_batch_file_line_captures_worktree_executable_mode(self, temp_git_repo):
        """discard --to --file --line should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_discard_to_batch("mode-batch", file="tool.sh", line_ids="1", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_discard_line_removes_single_addition(self, temp_git_repo):
        """Test discarding a single added line."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nNew line\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard line 1
        command_discard_line("1")

        # Check that the line was removed from working tree
        content = readme.read_text()
        assert content == "# Test\n"
        assert "New line" not in content

    def test_discard_line_restores_single_deletion(self, temp_git_repo):
        """Test discarding a single deleted line (restores it)."""
        readme = temp_git_repo / "README.md"
        readme.write_text("")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard line 1 (the deletion)
        command_discard_line("1")

        # Check that the line was restored in working tree
        content = readme.read_text()
        assert content == "# Test\n"

    def test_discard_line_with_range(self, temp_git_repo):
        """Test discarding a range of lines."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard lines 1-2
        command_discard_line("1-2")

        # Check that lines 1-2 were removed but line 3 remains
        content = readme.read_text()
        assert content == "# Test\nLine 3\n"

    def test_discard_line_partial_selection(self, temp_git_repo):
        """Test discarding only some lines from a hunk."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard only line 2
        command_discard_line("2")

        # Check that only line 2 was removed
        content = readme.read_text()
        assert content == "# Test\nLine 1\nLine 3\n"

    def test_discard_line_mixed_changes(self, temp_git_repo):
        """Test discarding from a hunk with both additions and deletions."""
        readme = temp_git_repo / "README.md"
        # Start with some content
        readme.write_text("# Test\nOld line 1\nOld line 2\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add content"], check=True, cwd=temp_git_repo, capture_output=True)

        # Make changes: delete old lines, add new lines
        readme.write_text("# Test\nNew line 1\nNew line 2\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard the first change (deletion of "Old line 1")
        command_discard_line("1")

        # Check that the deletion was undone (old line restored)
        content = readme.read_text()
        assert "Old line 1" in content

    def test_discard_line_invalid_file_path(self, temp_git_repo):
        """Test discarding from a file that doesn't exist in working tree."""
        # Create a file and commit it
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("content\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Delete the file (shows as deletions in diff)
        test_file.unlink()

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Try to discard - should fail because file doesn't exist
        with pytest.raises(CommandError):
            command_discard_line("1")

    def test_discard_line_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """discard --line should reject the selection when any ID is stale."""
        test_file = _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_discard_line("1,99")

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert test_file.read_text() == "base\nselected\n"


class TestCommandDiscardToBatch:
    """Tests for discard to batch command."""

    def test_discard_to_batch_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """discard --to --line should reject the selection when any ID is stale."""
        test_file = _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_discard_to_batch("invalid-lines", line_ids="1,99", quiet=True)

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert test_file.read_text() == "base\nselected\n"
        assert not batch_exists("invalid-lines")

    def test_discard_file_to_batch_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """discard --to --file --line should reject when any ID is stale."""
        test_file = _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_discard_to_batch(
                "invalid-lines",
                file="test.txt",
                line_ids="1,99",
                quiet=True,
            )

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert test_file.read_text() == "base\nselected\n"
        assert not batch_exists("invalid-lines")

    def test_discard_to_batch_as_rejects_mixed_valid_and_invalid_ids(self, temp_git_repo):
        """discard --to --line --as should reject when any ID is stale."""
        test_file = _prepare_single_line_change(temp_git_repo)

        with pytest.raises(CommandError) as exc_info:
            command_discard_line_as_to_batch(
                "invalid-lines",
                "1,99",
                "replacement",
                quiet=True,
            )

        assert "Line selection 1,99 is not valid for test.txt." in exc_info.value.message
        assert test_file.read_text() == "base\nselected\n"
        assert not batch_exists("invalid-lines")

    def test_discard_to_batch_saves_and_discards(self, temp_git_repo_with_session):
        """Test that discard to batch saves changes to batch and discards from working tree."""

        # The fixture creates file.txt with changes - start session to cache hunk
        command_start()

        command_discard_to_batch("test-batch")

        # Verify batch was created and contains the file
        files = list_batch_files("test-batch")
        assert "file.txt" in files

        # Verify file content was saved to batch
        content = read_file_from_batch("test-batch", "file.txt")
        assert content is not None
        assert "line1" in content

        # Verify changes were discarded from working tree
        file_txt = temp_git_repo_with_session / "file.txt"
        assert not file_txt.exists()  # File removed after discard

    def test_discard_to_batch_auto_creates_batch(self, temp_git_repo_with_session):
        """Test that discard to batch auto-creates batch if it doesn't exist."""

        # Start session to cache hunk
        command_start()

        # Batch doesn't exist yet
        assert not batch_exists("auto-batch")

        command_discard_to_batch("auto-batch")

        # Batch should now exist
        assert batch_exists("auto-batch")

        # Changes should be discarded
        file_txt = temp_git_repo_with_session / "file.txt"
        assert not file_txt.exists()

    def test_discard_file_to_batch_uses_scoped_ownership_metadata(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Discarding into an existing batch should not materialize ownership."""
        readme = temp_git_repo / "README.md"

        readme.write_text("# Test\none\n")
        command_start()
        fetch_next_change()
        command_discard_to_batch("metadata-batch", quiet=True)

        readme.write_text("# Test\ntwo\n")
        _reject_materialized_ownership_metadata(monkeypatch)

        command_discard_to_batch("metadata-batch", file="README.md", quiet=True)

        metadata = read_batch_metadata("metadata-batch")
        file_meta = metadata["files"]["README.md"]
        assert "presence_claims" in file_meta
        assert "deletions" in file_meta

    def test_discard_to_batch_hunk_captures_worktree_executable_mode(self, temp_git_repo):
        """discard --to should store chmod changes from the working tree."""
        tool_path = temp_git_repo / "tool.sh"
        tool_path.write_text("#!/bin/sh\necho base\n")
        tool_path.chmod(0o644)
        subprocess.run(["git", "add", "tool.sh"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add tool"], check=True, cwd=temp_git_repo, capture_output=True)

        tool_path.write_text("#!/bin/sh\necho base\necho added\n")
        tool_path.chmod(0o755)

        command_start()
        command_discard_to_batch("mode-batch", quiet=True)

        metadata = read_batch_metadata("mode-batch")
        assert metadata["files"]["tool.sh"]["mode"] == "100755"

    def test_discard_lines_to_batch_saves_partial_content(self, temp_git_repo):
        """Test that line-level discard to batch synthesizes correct partial content."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard only lines 1-2 to batch
        command_discard_to_batch("partial-batch", line_ids="1-2")

        # Verify batch contains partial content (original + lines 1-2)
        content = read_file_from_batch("partial-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        assert "Line 2" in content
        assert "# Test" in content

        # Verify only those lines were discarded from working tree
        working_content = readme.read_text()
        assert "Line 1" not in working_content
        assert "Line 2" not in working_content
        assert "Line 3" in working_content  # Not discarded
        assert "# Test" in working_content

    def test_discard_lines_to_batch_removes_from_working_tree(self, temp_git_repo):
        """Test that discarding lines removes them from working tree."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard only line 1 to batch
        command_discard_to_batch("discard-batch", line_ids="1")

        # Verify line 1 was removed from working tree
        working_content = readme.read_text()
        assert "Line 1" not in working_content
        assert "Line 2" in working_content
        assert "Line 3" in working_content

    def test_discard_lines_to_batch_accumulates(self, temp_git_repo):
        """Test that multiple line discard operations accumulate in batch."""

        # Modify README with multiple lines
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nLine 1\nLine 2\nLine 3\n")

        # Cache the hunk
        command_start()
        fetch_next_change()

        # Discard line 1
        command_discard_to_batch("accum-batch", line_ids="1")

        # Recalculate hunk after first discard
        recalculate_selected_hunk_for_file("README.md")

        # Discard line 2 (now line 1 in the renumbered hunk)
        command_discard_to_batch("accum-batch", line_ids="1")

        # Verify batch contains both lines
        content = read_file_from_batch("accum-batch", "README.md")
        assert content is not None
        assert "Line 1" in content
        assert "Line 2" in content

        # Verify both lines removed from working tree
        working_content = readme.read_text()
        assert "Line 1" not in working_content
        assert "Line 2" not in working_content
        assert "Line 3" in working_content

    def test_discard_replacement_lines_to_batch_displays_once_and_reapplies(self, temp_git_repo, capsys):
        """Discarding part of a replacement hunk to a batch can be applied back."""

        file_path = temp_git_repo / "file.txt"
        file_path.write_text("a\nb\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=temp_git_repo, capture_output=True)

        file_path.write_text("A\nB\n")

        command_start()
        capsys.readouterr()

        command_discard_to_batch("test", line_ids="1,3")

        captured = capsys.readouterr()
        assert captured.out.count("file.txt ::") == 1
        assert file_path.read_text() == "a\nB\n"

        command_apply_from_batch("test")

        assert file_path.read_text() == "A\nB\n"

    def test_discard_line_as_to_batch_stores_replacement_and_discards_original(self, temp_git_repo):
        """Discard --to --as should batch edited text but remove the local change."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nprint('debug')\n")

        command_start()
        fetch_next_change()

        command_discard_line_as_to_batch(
            "feature-batch",
            "1",
            'log("log worthy message that was debug print() before");',
        )

        assert readme.read_text() == "# Test\n"

        command_apply_from_batch("feature-batch")
        assert readme.read_text() == '# Test\nlog("log worthy message that was debug print() before");\n'

    def test_discard_line_as_to_batch_restores_working_tree_on_batch_error(self, temp_git_repo):
        """Batch persistence errors should not leave the rewritten file behind."""
        readme = temp_git_repo / "README.md"
        readme.write_text("# Test\nprint('debug')\n")

        command_start()
        fetch_next_change()

        with patch("git_stage_batch.commands.discard.add_file_to_batch", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                command_discard_line_as_to_batch(
                    "feature-batch",
                    "1",
                    'log("log worthy message that was debug print() before");',
                )

        assert readme.read_text() == "# Test\nprint('debug')\n"

    def test_discard_line_as_to_batch_handles_followup_replacement_in_same_session(self, temp_git_repo):
        """A second discard --as should refresh stale batch source state."""
        readme = temp_git_repo / "README.md"
        readme.write_text(
            "# Test\n"
            "line1\n"
            "line2\n"
            "line3\n"
            "line4\n"
            "line5\n"
            "line6\n"
            "line7\n"
            "line8\n"
        )
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Expand readme"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text(
            "# Test\n"
            "line1-edited\n"
            "line2\n"
            "line3\n"
            "line4\n"
            "line5\n"
            "line6\n"
            "line7\n"
            "line8-edited\n"
        )

        command_start()
        fetch_next_change()

        command_discard_line_as_to_batch(
            "feature-batch",
            "1",
            "staged1",
        )
        command_discard_line_as_to_batch(
            "feature-batch",
            "1",
            "staged8",
            file="README.md",
        )

        assert readme.read_text() == "# Test\nline1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n"

        command_apply_from_batch("feature-batch")
        assert readme.read_text() == "# Test\nstaged1\nline2\nline3\nline4\nline5\nline6\nline7\nstaged8\n"

    def test_discard_line_as_to_batch_replaces_disjoint_file_scoped_regions(self, temp_git_repo):
        """Discard replacement should accept one contiguous range across regions."""
        readme = temp_git_repo / "multi.txt"
        base_lines = [f"line{i}\n" for i in range(1, 41)]
        readme.write_text("".join(base_lines))
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
        readme.write_text("".join(rewritten_lines))

        command_start()
        staged_span = (
            ["stage-one-a\n", "stage-one-b\n"]
            + base_lines[5:20]
            + ["stage-two-a\n", "stage-two-b\n"]
            + base_lines[20:35]
            + ["stage-three-a\n", "stage-three-b\n"]
        )
        command_discard_line_as_to_batch(
            "feature-batch",
            "1-6",
            "".join(staged_span),
            file="multi.txt",
        )
        assert readme.read_text() == "".join(base_lines)
        assert batch_exists("feature-batch")

        command_apply_from_batch("feature-batch")
        assert readme.read_text() == (
            "".join(base_lines[:5])
            + "".join(staged_span)
            + "".join(base_lines[35:])
        )

    def test_discard_line_as_to_batch_trims_matching_edge_anchors(self, temp_git_repo):
        """Discard --to --line --as should accept unchanged edge anchors by default."""
        readme = temp_git_repo / "README.md"
        readme.write_text("keep1\nold\nkeep3\nkeep4\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Seed readme"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("keep1\nworking\nkeep3\nkeep4\n")

        command_start()
        fetch_next_change()

        command_discard_line_as_to_batch(
            "feature-batch",
            "1-2",
            "keep1\nstaged\nkeep3\nkeep4\n",
        )

        assert readme.read_text() == "keep1\nold\nkeep3\nkeep4\n"

        command_apply_from_batch("feature-batch")
        assert readme.read_text() == "keep1\nstaged\nkeep3\nkeep4\n"

    def test_discard_line_as_to_batch_no_edge_overlap_keeps_matching_edge_anchors(self, temp_git_repo):
        """Discard --to --line --as --no-edge-overlap should preserve duplicate anchors."""
        readme = temp_git_repo / "README.md"
        readme.write_text("keep1\nold\nkeep3\nkeep4\n")
        subprocess.run(["git", "add", "README.md"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Seed readme"], check=True, cwd=temp_git_repo, capture_output=True)

        readme.write_text("keep1\nworking\nkeep3\nkeep4\n")

        command_start()
        fetch_next_change()

        command_discard_line_as_to_batch(
            "feature-batch",
            "1-2",
            "keep1\nstaged\nkeep3\nkeep4\n",
            no_edge_overlap=True,
        )

        assert readme.read_text() == "keep1\nold\nstaged\nkeep3\nkeep4\nkeep3\nkeep4\n"

        command_apply_from_batch("feature-batch")
        assert readme.read_text() == "keep1\nkeep1\nstaged\nkeep3\nkeep4\nkeep3\nkeep4\n"
