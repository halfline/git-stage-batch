"""Tests for discard --file working-tree behavior."""

import subprocess

import pytest

from .conftest import git_stage_batch


class TestDiscardFile:
    """Tests for discard --file removing files and preserving batch content."""

    def test_discard_file_removes_new_file_from_working_tree(self, repo_with_changes):
        """Test that discarding a new file removes it from the working tree."""
        repo = repo_with_changes

        # Create a large new file (similar to utils/command.py with 607 lines)
        new_file = repo / "large_new_file.py"
        lines = [f"# Line {i}\ndef func_{i}(): pass\n" for i in range(1, 304)]
        new_file.write_text("".join(lines))

        # Add to git index (intent-to-add) so it shows up in git diff
        # This simulates files that were created but not fully staged
        subprocess.run(["git", "add", "-N", "large_new_file.py"], check=True, capture_output=True)

        # Verify file exists
        assert new_file.exists()
        original_content = new_file.read_text()

        # Start session
        git_stage_batch("start")

        # Navigate to large_new_file.py
        for _ in range(20):
            show = git_stage_batch("show", check=False)
            if show.returncode != 0:
                break
            if "large_new_file.py" in show.stdout:
                break
            git_stage_batch("skip")

        # Create a batch
        git_stage_batch("new", "test-batch")

        # Discard entire file to batch
        result = git_stage_batch("discard", "--file", "--to", "test-batch")
        assert result.returncode == 0

        assert not new_file.exists(), (
            f"file still exists after discard --file\n"
            f"File has {len(original_content.splitlines())} lines of content.\n"
            f"Expected: File removed from working tree\n"
            f"Actual: File still present with {len(new_file.read_text().splitlines()) if new_file.exists() else 0} lines"
        )

        # Verify batch contains the file
        show_result = git_stage_batch("show", "--from", "test-batch")
        assert "large_new_file.py" in show_result.stdout

    def test_discard_file_with_multiple_hunks(self, repo_with_changes):
        """Test discard --file with a file split into multiple hunks.

        The operation should remove every hunk for the file.
        """
        repo = repo_with_changes

        # Create a new file with many lines that will be split into multiple hunks
        test_file = repo / "test_large.py"
        content_lines = ['"""Test file."""\n\n']
        for i in range(350):
            content_lines.append(f'def test_{i}(): assert True\n\n')
        test_file.write_text("".join(content_lines))

        # Add to git index (intent-to-add) so it shows up in diff
        subprocess.run(["git", "add", "-N", "test_large.py"], check=True, capture_output=True)

        git_stage_batch("start")

        # Navigate to test_large.py
        for _ in range(20):
            show = git_stage_batch("show", check=False)
            if show.returncode != 0:
                break
            if "test_large.py" in show.stdout:
                break
            git_stage_batch("skip")

        git_stage_batch("new", "large-batch")

        # Discard entire file - should handle all hunks
        result = git_stage_batch("discard", "--file", "--to", "large-batch")
        assert result.returncode == 0

        # File should be completely removed
        assert not test_file.exists(), (
            "large file still exists after discard --file"
        )

    def test_batch_content_not_corrupted(self, repo_with_changes):
        """Test that batch doesn't get corrupted content from wrong files.

        During classification, batches got corrupted with mixed content from
        different files, like batch/match.py content appearing in multiple batches.
        """
        repo = repo_with_changes

        # Create two distinct files
        file_a = repo / "file_a.py"
        file_b = repo / "file_b.py"

        file_a.write_text("# MARKER_A\ndef func_a():\n    return 'A'\n")
        file_b.write_text("# MARKER_B\ndef func_b():\n    return 'B'\n")

        # Add to git index (intent-to-add) so they show up in diff
        subprocess.run(["git", "add", "-N", "file_a.py", "file_b.py"], check=True, capture_output=True)

        git_stage_batch("start")
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")

        # Navigate and discard file_a
        file_a_found = False
        for _ in range(20):
            show = git_stage_batch("show", check=False)
            if show.returncode != 0:
                break
            if "file_a.py" in show.stdout:
                git_stage_batch("discard", "--file", "--to", "batch-a")
                file_a_found = True
                break
            git_stage_batch("skip")

        assert file_a_found, "file_a.py not found in hunks"

        # Navigate and discard file_b
        file_b_found = False
        for _ in range(20):
            show = git_stage_batch("show", check=False)
            if show.returncode != 0:
                break
            if "file_b.py" in show.stdout:
                git_stage_batch("discard", "--file", "--to", "batch-b")
                file_b_found = True
                break
            git_stage_batch("skip")

        assert file_b_found, "file_b.py not found in hunks"

        # Verify no cross-contamination
        batch_a_result = git_stage_batch("show", "--from", "batch-a")
        batch_a = batch_a_result.stdout

        batch_b_result = git_stage_batch("show", "--from", "batch-b", check=False)
        if batch_b_result.returncode != 0:
            pytest.fail(f"batch-b is empty or doesn't exist. Stderr: {batch_b_result.stderr}")
        batch_b = batch_b_result.stdout

        assert "MARKER_A" in batch_a, "batch-a should contain file_a"
        assert "MARKER_B" not in batch_a, "batch-a contains file_b content"

        assert "MARKER_B" in batch_b, "batch-b should contain file_b"
        assert "MARKER_A" not in batch_b, "batch-b contains file_a content"
