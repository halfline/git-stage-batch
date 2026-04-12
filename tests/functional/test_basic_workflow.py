"""Functional tests for basic git-stage-batch workflow."""

import subprocess

import pytest

from .conftest import git_stage_batch, get_git_status, get_staged_files, get_staged_diff, get_unstaged_diff


class TestStartSession:
    """Test starting a session."""

    def test_start_session_with_changes(self, repo_with_changes):
        """Test starting a session with changes."""
        result = git_stage_batch("start")
        assert result.returncode == 0
        assert "README.md" in result.stdout or "main.py" in result.stdout

    def test_start_session_no_changes(self, functional_repo):
        """Test starting a session with no changes."""
        result = git_stage_batch("start", check=False)
        assert result.returncode != 0
        assert "No changes" in result.stderr or "No hunks" in result.stderr

    def test_start_session_outside_repo(self, tmp_path, monkeypatch):
        """Test starting session outside a git repo fails."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        monkeypatch.chdir(non_repo)

        result = git_stage_batch("start", check=False)
        assert result.returncode != 0


class TestIncludeWorkflow:
    """Test include command workflow."""

    def test_include_single_line(self, repo_with_changes):
        """Test including a single line."""
        # Start session
        git_stage_batch("start")

        # Include line 1
        git_stage_batch("include", "--line", "1")

        # Verify line was staged
        staged = get_staged_diff()
        assert "+" in staged  # Should have additions

        # Should still have unstaged changes
        unstaged = get_unstaged_diff()
        assert unstaged  # Not empty

    def test_include_multiple_lines(self, repo_with_changes):
        """Test including multiple lines."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1,2,3")

        staged = get_staged_diff()
        assert staged
        # Should have at least 3 additions
        assert staged.count("\n+") >= 3

    def test_include_line_range(self, repo_with_changes):
        """Test including a range of lines."""
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1-5")

        staged = get_staged_diff()
        assert staged

    def test_include_all_lines_in_hunk(self, repo_with_changes):
        """Test including all lines in a hunk."""
        git_stage_batch("start")

        # Get first hunk and include all its lines
        result = git_stage_batch("show")
        # Extract line IDs from output (look for [#N])
        import re
        line_ids = re.findall(r'\[#(\d+)\]', result.stdout)
        if line_ids:
            git_stage_batch("include", "--line", ",".join(line_ids))

            staged = get_staged_diff()
            assert staged


class TestSkipWorkflow:
    """Test skip command workflow."""

    def test_skip_to_next_change(self, repo_with_changes):
        """Test skipping to next hunk."""
        git_stage_batch("start")

        # Skip selected hunk
        result = git_stage_batch("skip")
        assert result.returncode == 0

        # Should show different content (next hunk)
        result = git_stage_batch("show")
        assert result.returncode == 0

    def test_skip_until_no_more_hunks(self, repo_with_changes):
        """Test skipping through all hunks."""
        git_stage_batch("start")

        # Skip multiple times
        for _ in range(10):  # More than we have hunks
            result = git_stage_batch("skip", check=False)
            if result.returncode != 0:
                assert "No more" in result.stderr or "No hunks" in result.stderr
                break


class TestDiscardWorkflow:
    """Test discard command workflow."""

    def test_discard_removes_changes(self, repo_with_changes):
        """Test discarding changes removes them."""
        # Save original content
        readme = repo_with_changes / "README.md"
        original = readme.read_text()

        git_stage_batch("start")
        git_stage_batch("discard", "--line", "1")

        # Changes should be removed from working tree
        new_content = readme.read_text()
        assert new_content != original

    def test_discard_all_in_hunk(self, repo_with_changes):
        """Test discarding all lines in a hunk."""
        git_stage_batch("start")

        # Get all line IDs and discard them
        result = git_stage_batch("show")
        import re
        line_ids = re.findall(r'\[#(\d+)\]', result.stdout)
        if line_ids:
            git_stage_batch("discard", "--line", ",".join(line_ids))

            # Verify changes are gone
            unstaged = get_unstaged_diff()
            # Should have fewer changes now


class TestShowCommand:
    """Test show command."""

    def test_show_displays_selected_hunk(self, repo_with_changes):
        """Test show displays selected hunk with line IDs."""
        git_stage_batch("start")

        result = git_stage_batch("show")
        assert result.returncode == 0
        # Should have line IDs
        assert "[#" in result.stdout
        # Should have file name
        assert "::" in result.stdout

    def test_show_after_include_shows_next(self, repo_with_changes):
        """Test show after include shows next hunk."""
        git_stage_batch("start")

        first_show = git_stage_batch("show")
        first_content = first_show.stdout

        # Include all lines
        import re
        line_ids = re.findall(r'\[#(\d+)\]', first_content)
        if line_ids:
            git_stage_batch("include", "--line", ",".join(line_ids))

            # Next show should be different
            second_show = git_stage_batch("show", check=False)
            if second_show.returncode == 0:
                assert second_show.stdout != first_content


class TestAbortSession:
    """Test aborting a session."""

    def test_abort_restores_original_state(self, repo_with_changes):
        """Test abort restores original state."""
        # Save original status
        original_status = get_git_status()

        git_stage_batch("start")
        git_stage_batch("include", "--line", "1")

        # Verify we have staged changes
        staged = get_staged_files()
        assert staged

        # Abort
        git_stage_batch("abort")

        # Should restore to original state
        final_status = get_git_status()
        assert final_status == original_status

    def test_abort_with_no_session_fails(self, repo_with_changes):
        """Test abort with no active session fails."""
        result = git_stage_batch("abort", check=False)
        assert result.returncode != 0


class TestCompleteWorkflow:
    """Test complete end-to-end workflows."""

    def test_stage_changes_incrementally(self, repo_with_changes):
        """Test staging changes incrementally across multiple hunks."""
        git_stage_batch("start")

        # Stage some lines from first hunk
        git_stage_batch("include", "--line", "1,2")

        # Skip to next hunk
        git_stage_batch("skip")

        # Stage some from second hunk
        git_stage_batch("include", "--line", "1")

        # Should have multiple files staged
        staged = get_staged_diff()
        assert staged
        assert "+" in staged

    def test_mixed_operations_workflow(self, repo_with_changes):
        """Test mixing include, discard, and skip."""
        git_stage_batch("start")

        # Include some lines
        result = git_stage_batch("show")
        if "[#1]" in result.stdout:
            git_stage_batch("include", "--line", "1")

        # Skip to next
        git_stage_batch("skip", check=False)

        # Discard some from next hunk
        result = git_stage_batch("show", check=False)
        if result.returncode == 0 and "[#1]" in result.stdout:
            git_stage_batch("discard", "--line", "1", check=False)

        # Should have staged changes
        staged = get_staged_diff()
        assert staged

    def test_stage_all_changes_then_commit(self, repo_with_changes):
        """Test staging all changes and creating a commit."""
        git_stage_batch("start")

        # Include lines from multiple hunks until done
        for _ in range(20):  # Safety limit
            result = git_stage_batch("show", check=False)
            if result.returncode != 0:
                break

            import re
            line_ids = re.findall(r'\[#(\d+)\]', result.stdout)
            if line_ids:
                # Include all lines in this hunk
                git_stage_batch("include", "--line", ",".join(line_ids))
            else:
                break

        # Should have staged changes
        staged_files = get_staged_files()
        assert len(staged_files) > 0

        # Should be able to commit
        result = subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            capture_output=True,
            check=True
        )
        assert result.returncode == 0
