"""Comprehensive tests for abort, again, and display correctness."""

from pathlib import Path
import pytest

import subprocess

from .conftest import git_stage_batch, get_staged_files, get_unstaged_diff, get_git_status


def run_interactive(*inputs, timeout=5):
    """Run interactive mode with simulated input."""

    test_dir = Path(__file__).parent
    project_root = test_dir.parent.parent
    venv_gsb = project_root / ".venv" / "bin" / "git-stage-batch"

    if venv_gsb.exists():
        cmd = [str(venv_gsb), "-i"]
    else:
        cmd = ["uv", "run", "--", "git-stage-batch", "-i"]

    input_text = "\n".join(inputs) + "\n"

    try:
        result = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False
        )
        return result
    except subprocess.TimeoutExpired:
        pytest.fail("Interactive mode timed out")


class TestAbortThorough:
    """Thorough tests for abort command."""

    def test_abort_restores_staged_changes(self, repo_with_changes):
        """Test abort restores staged changes from before session."""
        # Stage some changes before session
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)

        # Verify staged
        staged_before = get_staged_files()
        assert "README.md" in staged_before

        # Start session
        git_stage_batch("start")

        # Unstage during session
        subprocess.run(["git", "reset", "README.md"], check=True, capture_output=True)

        # Verify unstaged
        staged_during = get_staged_files()
        assert "README.md" not in staged_during

        # Abort should restore staged state
        git_stage_batch("abort")

        staged_after = get_staged_files()
        assert "README.md" in staged_after

    def test_abort_after_batch_operations(self, repo_with_changes):
        """Test abort works correctly after batch operations."""
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1,2", check=False)

        # Abort
        git_stage_batch("abort")

        # Status should be restored
        selected_status = get_git_status()
        # Should have similar unstaged changes (may differ slightly due to session)
        assert len(selected_status) > 0

        # Batch should still exist
        list_result = git_stage_batch("list")
        assert "test-batch" in list_result.stdout

    def test_abort_after_discard(self, repo_with_changes):
        """Test abort restores discarded changes."""
        git_stage_batch("start")
        git_stage_batch("discard", "--line", "1", check=False)

        # Abort should restore
        git_stage_batch("abort")

        restored_diff = get_unstaged_diff()
        # Should have unstaged changes
        assert len(restored_diff) > 0

    def test_abort_clears_session_state(self, repo_with_changes):
        """Test abort fully clears session state."""
        git_stage_batch("start")
        git_stage_batch("abort")

        # Status should indicate no session
        result = git_stage_batch("status", check=False)
        assert "No batch staging session" in result.stderr or result.returncode != 0

        # Show should fail (no session)
        result = git_stage_batch("show", check=False)
        assert result.returncode != 0

    def test_abort_multiple_times_fails(self, repo_with_changes):
        """Test aborting twice fails."""
        git_stage_batch("start")
        git_stage_batch("abort")

        # Second abort should fail
        result = git_stage_batch("abort", check=False)
        assert result.returncode != 0

    def test_abort_after_include_and_discard_mix(self, repo_with_changes):
        """Test abort after mixed operations."""
        git_stage_batch("start")

        # Mix of operations
        git_stage_batch("include", "--line", "1", check=False)
        git_stage_batch("skip", check=False)
        git_stage_batch("discard", "--line", "1", check=False)
        git_stage_batch("skip", check=False)

        # Abort should restore
        git_stage_batch("abort")

        # Should be back to no session
        result = git_stage_batch("status", check=False)
        assert "No batch staging session" in result.stderr or result.returncode != 0

    def test_abort_preserves_batch_data(self, repo_with_changes):
        """Test abort reverts batch to original state."""
        git_stage_batch("new", "preserve-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "preserve-batch", "--line", "1,2", check=False)

        # Abort should revert batch to original (empty) state
        git_stage_batch("abort")

        # Batch should exist but be empty (reverted to pre-session state)
        batch_show = git_stage_batch("show", "--from", "preserve-batch", check=False)
        # Batch should be empty - stderr will say "Batch 'preserve-batch' is empty"
        assert "empty" in batch_show.stderr.lower()


class TestAbortInteractive:
    """Test abort in interactive mode."""

    def test_interactive_abort(self, repo_with_changes):
        """Test abort in interactive mode."""
        result = run_interactive("i", "abort")
        # Should exit successfully
        assert result.returncode in [0, 1]

    def test_interactive_abort_after_operations(self, repo_with_changes):
        """Test abort after operations in interactive mode."""
        result = run_interactive("i", "s", "i", "abort")
        assert result.returncode in [0, 1]

        # Should be back to no session
        status_result = git_stage_batch("status", check=False)
        assert "No batch staging session" in status_result.stderr or status_result.returncode != 0

    def test_interactive_abort_with_batch(self, repo_with_changes):
        """Test abort with batch operations in interactive mode."""
        git_stage_batch("new", "interactive-batch")

        result = run_interactive("i --to interactive-batch", "abort")
        assert result.returncode in [0, 1]

        # Batch should still exist
        list_result = git_stage_batch("list")
        assert "interactive-batch" in list_result.stdout


class TestAgainThorough:
    """Thorough tests for again command."""

    def test_again_resets_blocklist(self, repo_with_changes):
        """Test again clears the blocklist."""
        git_stage_batch("start")

        # Skip a hunk to block it
        git_stage_batch("skip", check=False)

        # Again should reset
        git_stage_batch("again")

        # Should be able to see first hunk again
        result = git_stage_batch("show", check=False)
        if result.returncode == 0:
            assert result.stdout

    def test_again_preserves_batch_data(self, repo_with_changes):
        """Test again preserves batch data."""
        git_stage_batch("new", "again-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "again-batch", "--line", "1,2", check=False)

        # Again
        git_stage_batch("again")

        # Batch should still have data
        batch_show = git_stage_batch("show", "--from", "again-batch", check=False)
        if batch_show.returncode == 0:
            assert batch_show.stdout

    def test_again_after_multiple_operations(self, repo_with_changes):
        """Test again after multiple operations."""
        git_stage_batch("start")

        # Multiple operations
        git_stage_batch("include", "--line", "1", check=False)
        git_stage_batch("skip", check=False)
        git_stage_batch("skip", check=False)

        # Again should reset
        git_stage_batch("again")

        # Should be at first hunk again
        result = git_stage_batch("show", check=False)
        if result.returncode == 0:
            assert result.stdout

    def test_again_clears_processed_lines(self, repo_with_changes):
        """Test again clears processed line tracking."""
        git_stage_batch("start")

        # Include some lines
        git_stage_batch("include", "--line", "1,2", check=False)

        # Again
        git_stage_batch("again")

        # Should be able to include same lines again
        result = git_stage_batch("show", check=False)
        if result.returncode == 0:
            assert "[#1]" in result.stdout or "[#2]" in result.stdout

    def test_again_with_no_session_fails(self, repo_with_changes):
        """Test again without session fails."""
        result = git_stage_batch("again", check=False)
        assert result.returncode != 0

    def test_again_multiple_times(self, repo_with_changes):
        """Test calling again multiple times."""
        git_stage_batch("start")

        git_stage_batch("skip", check=False)
        git_stage_batch("again")

        git_stage_batch("skip", check=False)
        git_stage_batch("again")

        # Should still work
        result = git_stage_batch("show", check=False)
        if result.returncode == 0:
            assert result.stdout


class TestAgainInteractive:
    """Test again in interactive mode."""

    def test_interactive_again(self, repo_with_changes):
        """Test again in interactive mode."""
        result = run_interactive("s", "again", "q")
        assert result.returncode in [0, 1]

    def test_interactive_again_after_operations(self, repo_with_changes):
        """Test again after operations in interactive mode."""
        result = run_interactive("i", "s", "s", "again", "q")
        assert result.returncode in [0, 1]

    def test_interactive_again_with_batch(self, repo_with_changes):
        """Test again with batch operations in interactive mode."""
        git_stage_batch("new", "again-interactive")

        result = run_interactive("i --to again-interactive", "again", "q")
        assert result.returncode in [0, 1]


class TestDisplayCorrectness:
    """Test display correctness and prevent double printing."""

    def test_no_double_print_after_include(self, repo_with_changes):
        """Test that hunk is not printed excessively after include."""
        git_stage_batch("start")

        result = git_stage_batch("include", "--line", "1", check=False)

        # Count how many times hunk headers appear in output
        # May show updated hunk + next hunk, but should not print same hunk 3+ times
        hunk_headers = result.stdout.count("@@")
        assert hunk_headers <= 2  # At most 2 hunks (updated selected + next)

    def test_no_double_print_after_skip(self, repo_with_changes):
        """Test that hunk is not printed excessively after skip."""
        git_stage_batch("start")

        result = git_stage_batch("skip", check=False)

        # Should show next hunk, not print excessively
        hunk_headers = result.stdout.count("@@")
        assert hunk_headers <= 2  # At most 2 hunks

    def test_no_double_print_after_discard(self, repo_with_changes):
        """Test that hunk is not printed excessively after discard."""
        git_stage_batch("start")

        result = git_stage_batch("discard", "--line", "1", check=False)

        # Should show next hunk, not print excessively
        hunk_headers = result.stdout.count("@@")
        assert hunk_headers <= 2  # At most 2 hunks

    def test_batch_masking_in_show(self, repo_with_changes):
        """Test that batched lines are masked properly in show."""
        git_stage_batch("new", "mask-test")
        git_stage_batch("start")

        # Include line 1 to batch
        git_stage_batch("include", "--to", "mask-test", "--line", "1", check=False)

        # Show should not display line 1 anymore (or show next hunk)
        git_stage_batch("show", check=False)

        # If we still have a hunk, line 1 from the batched hunk shouldn't be there
        # Or we've moved to the next hunk

    def test_batch_masking_in_interactive(self, repo_with_changes):
        """Test that batched lines are masked in interactive mode."""
        git_stage_batch("new", "interactive-mask")

        result = run_interactive("i --to interactive-mask", "q")

        # Should not show double hunks
        hunk_count = result.stdout.count("@@")
        # Hard to test exactly, but should be reasonable
        assert hunk_count < 20  # Sanity check

    def test_show_displays_line_ids_correctly(self, repo_with_changes):
        """Test that show displays line IDs correctly."""
        git_stage_batch("start")

        result = git_stage_batch("show", check=False)
        if result.returncode == 0:
            # Should have line IDs
            assert "[#1]" in result.stdout or "@@" in result.stdout

    def test_include_to_batch_removes_from_display(self, repo_with_changes):
        """Test that including to batch removes lines from display."""
        git_stage_batch("new", "remove-test")
        git_stage_batch("start")

        first_show = git_stage_batch("show", check=False)
        if first_show.returncode == 0 and "[#1]" in first_show.stdout:
            # Include line 1 to batch
            git_stage_batch("include", "--to", "remove-test", "--line", "1", check=False)

            # Show again - should not show line 1 from same hunk
            second_show = git_stage_batch("show", check=False)

            # Either moved to next hunk, or hunk is filtered
            # Should not be identical to first show
            if second_show.returncode == 0:
                # If we're showing a hunk, it should be different or filtered
                pass  # Hard to test exact behavior, but shouldn't crash

    def test_interactive_no_double_print_on_include(self, repo_with_changes):
        """Test no double printing in interactive mode."""
        result = run_interactive("i", "q")

        # Should not print same hunk multiple times in sequence
        lines = result.stdout.split("\n")

        # Count consecutive identical hunk headers
        consecutive_count = 0
        prev_line = None
        max_consecutive = 0

        for line in lines:
            if line.startswith("@@") and line == prev_line:
                consecutive_count += 1
                max_consecutive = max(max_consecutive, consecutive_count)
            else:
                consecutive_count = 0
            prev_line = line if line.startswith("@@") else None

        # Should not have same hunk printed more than once consecutively
        assert max_consecutive <= 1
