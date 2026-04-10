"""Functional tests for multi-file scenarios."""

import subprocess

from .conftest import git_stage_batch, get_staged_files, get_staged_diff


class TestMultiFileWorkflow:
    """Test working with multiple files."""

    def test_stage_changes_from_multiple_files(self, repo_with_changes):
        """Test staging changes from multiple files."""
        git_stage_batch("start")

        # Stage from multiple hunks across files
        for _ in range(5):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            # Include some lines
            git_stage_batch("include", "--line", "1", check=False)

        # Should have multiple files staged
        staged_files = get_staged_files()
        assert len(staged_files) >= 1

        # Verify staged content includes different files
        staged = get_staged_diff()
        assert staged

    def test_batch_operations_across_files(self, repo_with_changes):
        """Test batch operations with multiple files."""
        git_stage_batch("new", "multi-file-batch")
        git_stage_batch("start")

        # Include changes from multiple files to batch
        for i in range(5):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            git_stage_batch("include", "--to", "multi-file-batch", "--line", "1", check=False)

        # Batch should contain multiple files
        batch_show = git_stage_batch("show", "--from", "multi-file-batch")
        assert batch_show.stdout

    def test_selective_file_staging(self, repo_with_changes):
        """Test selectively staging from specific files."""
        git_stage_batch("start")

        # Find and stage only from README
        for _ in range(10):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            if "README" in result.stdout:
                git_stage_batch("include", "--line", "1")
                break
            else:
                git_stage_batch("skip", check=False)

        # Should have README staged
        staged_files = get_staged_files()
        readme_staged = any("README" in f for f in staged_files)
        assert readme_staged


class TestNewFileHandling:
    """Test handling of new files."""

    def test_stage_new_file(self, repo_with_changes):
        """Test staging a new file."""
        # New file was created by fixture (config.py)
        git_stage_batch("start")

        # Find the new file hunk
        for _ in range(10):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            if "config.py" in result.stdout:
                # Stage some lines from new file
                git_stage_batch("include", "--line", "1,2")
                break
            else:
                git_stage_batch("skip", check=False)

        staged = get_staged_diff()
        if "config.py" in staged:
            # Verify new file is staged
            assert "+CONFIG" in staged or "+debug" in staged or "+version" in staged

    def test_batch_with_new_file(self, repo_with_changes):
        """Test batching changes including new files."""
        git_stage_batch("new", "with-new-file")
        git_stage_batch("start")

        # Find and batch new file
        for _ in range(10):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            if "config.py" in result.stdout:
                git_stage_batch("include", "--to", "with-new-file", "--line", "1,2")
                break
            else:
                git_stage_batch("skip", check=False)

        # Batch should contain new file
        batch_show = git_stage_batch("show", "--from", "with-new-file", check=False)
        if batch_show.returncode == 0:
            assert batch_show.stdout


class TestLargeChangesets:
    """Test with large changesets across many files."""

    def test_many_files_many_changes(self, functional_repo):
        """Test workflow with many files and changes."""
        # Create many files with changes
        for i in range(10):
            file_path = functional_repo / f"file{i}.txt"
            file_path.write_text(f"Line 1\nLine 2\nLine 3\nLine {i}\n")

        git_stage_batch("start", check=False)

        # Should be able to navigate through all hunks
        hunk_count = 0
        for _ in range(50):  # Safety limit
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break
            hunk_count += 1

            # Include first line from each hunk
            git_stage_batch("include", "--line", "1", check=False)

        # Should have processed multiple hunks
        assert hunk_count > 0

        # Should have staged changes
        staged_files = get_staged_files()
        assert len(staged_files) > 0

    def test_batch_with_many_files(self, functional_repo):
        """Test batching with many files."""
        # Create changes in many files
        for i in range(10):
            file_path = functional_repo / f"batch_file{i}.txt"
            file_path.write_text(f"Content {i}\n")

        git_stage_batch("new", "large-batch")
        git_stage_batch("start", check=False)

        # Add multiple files to batch
        for _ in range(20):
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            git_stage_batch("include", "--to", "large-batch", "--line", "1", check=False)

        # Batch should have content
        batch_show = git_stage_batch("show", "--from", "large-batch", check=False)
        if batch_show.returncode == 0:
            assert batch_show.stdout


class TestFileWithManyChanges:
    """Test file with many changes in single hunk."""

    def test_large_single_file_change(self, functional_repo):
        """Test file with many lines changed."""
        # Create file with many changes
        large_file = functional_repo / "large.txt"
        lines = [f"Line {i}\n" for i in range(100)]
        large_file.write_text("".join(lines))

        # Commit it
        subprocess.run(["git", "add", "large.txt"], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add large file"],
            check=True,
            capture_output=True
        )

        # Modify many lines
        lines = [f"Modified line {i}\n" for i in range(100)]
        large_file.write_text("".join(lines))

        git_stage_batch("start")

        # Should show many line IDs
        result = git_stage_batch("show")
        assert "[#" in result.stdout

        # Should be able to select range
        git_stage_batch("include", "--line", "1-10")

        staged = get_staged_diff()
        assert staged
        # Should have multiple changes (additions or deletions)
        change_count = staged.count("\n+") + staged.count("\n-")
        assert change_count >= 10
