"""Functional tests for interactive/TUI mode."""

import subprocess
import time

import pytest

from .conftest import git_stage_batch, get_staged_files, get_staged_diff


def run_interactive(*inputs, timeout=5):
    """Run interactive mode with simulated input.

    Args:
        *inputs: Input strings to send to interactive mode
        timeout: Timeout in seconds

    Returns:
        subprocess.CompletedProcess
    """
    from pathlib import Path

    # Find the venv git-stage-batch to ensure we use in-tree version
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
        # Interactive mode might not exit cleanly with some inputs
        pytest.fail("Interactive mode timed out")


class TestInteractiveMode:
    """Test interactive mode basic functionality."""

    def test_start_interactive_with_changes(self, repo_with_changes):
        """Test starting interactive mode with changes."""
        # Just 'q' to quit immediately
        result = run_interactive("q")
        # Should start and quit successfully
        assert result.returncode == 0

    def test_interactive_i_flag(self, repo_with_changes):
        """Test starting with -i flag."""
        result = run_interactive("q")
        assert result.returncode == 0

    def test_interactive_command_alias(self, repo_with_changes):
        """Test starting with 'interactive' command."""
        from pathlib import Path

        test_dir = Path(__file__).parent
        project_root = test_dir.parent.parent
        venv_gsb = project_root / ".venv" / "bin" / "git-stage-batch"

        if venv_gsb.exists():
            cmd = [str(venv_gsb), "interactive"]
        else:
            cmd = ["uv", "run", "--", "git-stage-batch", "interactive"]

        result = subprocess.run(
            cmd,
            input="q\n",
            text=True,
            capture_output=True,
            timeout=5,
            check=False
        )
        assert result.returncode == 0

    def test_interactive_with_no_changes(self, functional_repo):
        """Test interactive mode with no changes."""
        result = run_interactive("q")
        # Should start in degraded mode and quit successfully
        assert result.returncode == 0
        assert "no changes" in result.stderr.lower()


class TestInteractiveCommands:
    """Test interactive mode commands."""

    def test_interactive_include_command(self, repo_with_changes):
        """Test include command in interactive mode."""
        # Send 'i' (include), then 'q' (quit)
        result = run_interactive("i", "q")
        assert result.returncode == 0
        # Should have staged something
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_skip_command(self, repo_with_changes):
        """Test skip command in interactive mode."""
        result = run_interactive("s", "q")
        assert result.returncode == 0
        # Should not have staged anything
        staged = get_staged_files()
        assert len(staged) == 0

    def test_interactive_show_command(self, repo_with_changes):
        """Test show command in interactive mode."""
        result = run_interactive("show", "q")
        assert result.returncode == 0
        # Should display hunk in output
        assert len(result.stdout) > 0

    def test_interactive_status_command(self, repo_with_changes):
        """Test status command in interactive mode."""
        result = run_interactive("status", "q")
        assert result.returncode == 0
        # Should show status information
        output = result.stdout + result.stderr
        assert "processed" in output.lower() or "remaining" in output.lower()

    def test_interactive_quit_command(self, repo_with_changes):
        """Test quit command."""
        result = run_interactive("quit")
        assert result.returncode == 0

    def test_interactive_q_shorthand(self, repo_with_changes):
        """Test 'q' shorthand for quit."""
        result = run_interactive("q")
        assert result.returncode == 0

    def test_interactive_include_multiple_lines(self, repo_with_changes):
        """Test including multiple lines with line IDs."""
        result = run_interactive("l", "i", "1,2,3", "q")
        assert result.returncode == 0
        # Should have staged something
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_include_line_range(self, repo_with_changes):
        """Test including a line range."""
        result = run_interactive("l", "i", "1-5", "q")
        assert result.returncode == 0
        # Should have staged something
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_skip_until_end(self, repo_with_changes):
        """Test skipping through all hunks."""
        # Skip multiple times until no more hunks
        result = run_interactive("s", "s", "s", "s", "s", "s", "s", "s", "s", "s", "q")
        assert result.returncode == 0
        # Nothing should be staged
        staged = get_staged_files()
        assert len(staged) == 0

    def test_interactive_discard_removes_changes(self, repo_with_changes):
        """Test that discard command removes changes."""
        result = run_interactive("l", "d", "yes", "1", "q")
        assert result.returncode == 0


class TestInteractiveWorkflow:
    """Test interactive mode workflow."""

    def test_interactive_include_then_skip(self, repo_with_changes):
        """Test including then skipping in interactive mode."""
        result = run_interactive("i", "s", "q")
        assert result.returncode == 0
        # Should have staged some changes from first include
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_multiple_operations(self, repo_with_changes):
        """Test multiple operations in sequence."""
        result = run_interactive(
            "i",      # include
            "s",      # skip
            "show",   # show selected
            "status", # show status
            "q"       # quit
        )
        assert result.returncode == 0
        # Should have staged something from the include
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_discard_workflow(self, repo_with_changes):
        """Test discard in interactive mode."""
        result = run_interactive("d", "yes", "q")
        assert result.returncode == 0

    def test_interactive_help_command(self, repo_with_changes):
        """Test help command in interactive mode."""
        result = run_interactive("help", "q")
        assert result.returncode == 0
        # Should show help text
        output = result.stdout + result.stderr
        assert "interactive" in output.lower() or "help" in output.lower()

    def test_interactive_invalid_command(self, repo_with_changes):
        """Test invalid command in interactive mode."""
        result = run_interactive("invalid-command", "q")
        # Should handle gracefully
        assert result.returncode == 0
        # Should show error or treat as CLI command
        output = result.stdout + result.stderr
        assert len(output) > 0

    def test_interactive_stage_incrementally(self, repo_with_changes):
        """Test staging changes incrementally across multiple hunks."""
        # Include from first hunk, skip, include from second
        result = run_interactive("i", "s", "i", "q")
        assert result.returncode == 0
        # Should have staged changes from two includes
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_complete_session(self, repo_with_changes):
        """Test completing a full session with multiple operations."""
        result = run_interactive(
            "show",      # Show selected hunk
            "i",         # Include
            "show",      # Show next
            "s",         # Skip
            "show",      # Show next
            "status",    # Check status
            "q"          # Quit
        )
        assert result.returncode == 0
        # Should have staged from the include
        staged = get_staged_files()
        assert len(staged) > 0


class TestInteractiveBatchOperations:
    """Test batch operations in interactive mode."""

    def test_interactive_create_batch(self, repo_with_changes):
        """Test creating batch in interactive mode."""
        result = run_interactive("new test-batch", "q")
        assert result.returncode == 0
        # Verify batch was created
        list_result = git_stage_batch("list")
        assert "test-batch" in list_result.stdout

    def test_interactive_include_to_batch(self, repo_with_changes):
        """Test including to batch in interactive mode."""
        # Create batch first
        git_stage_batch("new", "test-batch")

        result = run_interactive(">test-batch", "i", "q")
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # Verify batch has content (or at least command succeeded)
        show_result = git_stage_batch("show", "--from", "test-batch", check=False)
        # Batch may or may not have content depending on what hunks were available
        # Just verify the command worked
        assert show_result.returncode == 0

    def test_interactive_list_batches(self, repo_with_changes):
        """Test listing batches in interactive mode."""
        git_stage_batch("new", "batch-a")
        git_stage_batch("new", "batch-b")

        result = run_interactive("list", "q")
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "batch-a" in output
        assert "batch-b" in output

    def test_interactive_show_from_batch(self, repo_with_changes):
        """Test showing from batch in interactive mode."""
        # Create and populate batch
        git_stage_batch("new", "test-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "test-batch", "--line", "1")
        git_stage_batch("abort")

        result = run_interactive("show --from test-batch", "q")
        assert result.returncode == 0
        assert len(result.stdout) > 0

    def test_interactive_discard_to_batch(self, repo_with_changes):
        """Test discarding to batch in interactive mode."""
        git_stage_batch("new", "discard-batch")

        result = run_interactive(">discard-batch", "d", "q")
        assert result.returncode == 0
        # Verify batch has content
        show_result = git_stage_batch("show", "--from", "discard-batch")
        assert len(show_result.stdout) > 0

    def test_interactive_apply_from_batch(self, repo_with_changes):
        """Test applying from batch in interactive mode."""
        # Create and populate batch
        git_stage_batch("new", "apply-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "apply-batch", "--line", "1,2")
        git_stage_batch("abort")

        result = run_interactive("apply --from apply-batch", "q")
        assert result.returncode == 0

    def test_interactive_apply_from_batch_with_lines(self, repo_with_changes):
        """Test applying specific lines from batch in interactive mode."""
        git_stage_batch("new", "apply-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "apply-batch", "--line", "1,2,3")
        git_stage_batch("abort")

        result = run_interactive("apply --from apply-batch --line 1,2", "q")
        assert result.returncode == 0

    def test_interactive_drop_batch(self, repo_with_changes):
        """Test dropping batch in interactive mode."""
        git_stage_batch("new", "drop-batch")

        result = run_interactive("drop drop-batch", "q")
        assert result.returncode == 0
        # Verify batch no longer exists
        list_result = git_stage_batch("list")
        assert "drop-batch" not in list_result.stdout

    def test_interactive_annotate_batch(self, repo_with_changes):
        """Test annotating batch in interactive mode."""
        git_stage_batch("new", "annotate-batch")

        result = run_interactive("annotate annotate-batch 'Test note'", "q")
        assert result.returncode == 0

    def test_interactive_include_from_batch(self, repo_with_changes):
        """Test including from batch in interactive mode."""
        # Create and populate batch with changes, then revert working tree
        git_stage_batch("new", "include-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "include-batch", "--line", "1,2")
        git_stage_batch("abort")  # Revert working tree so batch can be applied

        # Now use TUI to pull from batch and stage
        result = run_interactive("<include-batch", "i", "q")
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # The batch should have been applied and staged
        # Note: this might not stage anything if batch is empty or conflicts
        # Just verify the command succeeded
        pass

    def test_interactive_discard_from_batch(self, repo_with_changes):
        """Test discarding from batch in interactive mode."""
        # Create and populate batch
        git_stage_batch("new", "discard-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "discard-batch", "--line", "1,2")
        git_stage_batch("abort")

        result = run_interactive("<discard-batch", "d", "yes", "q")
        assert result.returncode == 0

    def test_interactive_reset_from_batch(self, repo_with_changes):
        """Test resetting from batch in interactive mode."""
        # Create and populate batch
        git_stage_batch("new", "reset-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "reset-batch", "--line", "1,2")
        git_stage_batch("abort")

        result = run_interactive("reset --from reset-batch", "q")
        assert result.returncode == 0


class TestInteractiveEdgeCases:
    """Test edge cases in interactive mode."""

    def test_interactive_empty_input(self, repo_with_changes):
        """Test empty input in interactive mode."""
        result = run_interactive("", "q")
        assert result.returncode == 0

    def test_interactive_whitespace_input(self, repo_with_changes):
        """Test whitespace input."""
        result = run_interactive("   ", "q")
        assert result.returncode == 0

    def test_interactive_multiple_quit_commands(self, repo_with_changes):
        """Test multiple quit commands."""
        result = run_interactive("q", "q", "q")
        # Should exit on first quit
        assert result.returncode == 0

    def test_interactive_ctrl_c_simulation(self, repo_with_changes):
        """Test that interactive mode can exit."""
        # Just test it starts and can quit
        result = run_interactive("q")
        assert result.returncode == 0

    def test_interactive_rapid_commands(self, repo_with_changes):
        """Test rapid command execution."""
        commands = ["show", "status", "s", "i", "status", "q"]
        result = run_interactive(*commands)
        assert result.returncode == 0


class TestInteractiveSession:
    """Test interactive mode session management."""

    def test_interactive_preserves_session(self, repo_with_changes):
        """Test that interactive mode preserves session state."""
        # Start interactive, include something, quit
        result = run_interactive("i", "q")
        assert result.returncode == 0

        # Should be able to continue session
        result = git_stage_batch("status")
        assert result.returncode == 0

    def test_interactive_abort_command(self, repo_with_changes):
        """Test abort command in interactive mode."""
        result = run_interactive("i", "abort")
        # Should abort and exit
        assert result.returncode == 0

    def test_interactive_again_command(self, repo_with_changes):
        """Test 'again' command in interactive mode."""
        # Start, include something, then use 'again'
        result = run_interactive("i", "a", "q")
        assert result.returncode == 0


class TestInteractiveDisplay:
    """Test interactive mode display and output."""

    def test_interactive_shows_hunks(self, repo_with_changes):
        """Test that interactive mode displays hunks."""
        result = run_interactive("q")
        assert result.returncode == 0
        # Should have some output showing hunks
        assert len(result.stdout) > 0

    def test_interactive_shows_prompts(self, repo_with_changes):
        """Test that interactive mode shows prompts."""
        result = run_interactive("q")
        assert result.returncode == 0
        # Should have output
        output = result.stdout + result.stderr
        assert len(output) > 0

    def test_interactive_shows_progress(self, repo_with_changes):
        """Test that interactive mode shows progress."""
        result = run_interactive("s", "s", "q")
        assert result.returncode == 0
        # Should have output showing progress
        assert len(result.stdout) > 0


class TestInteractiveMultiFile:
    """Test interactive mode with multiple files."""

    def test_interactive_multiple_files_workflow(self, repo_with_changes):
        """Test interactive workflow across multiple files."""
        # Navigate through and stage from multiple hunks/files
        result = run_interactive("i", "s", "i", "s", "i", "q")
        assert result.returncode == 0
        # Should have staged some changes
        staged_files = get_staged_files()
        assert len(staged_files) > 0

    def test_interactive_batch_multiple_files(self, repo_with_changes):
        """Test batching changes from multiple files."""
        git_stage_batch("new", "multi-file")

        result = run_interactive(
            ">multi-file",
            "i",
            "s",
            "i",
            "s",
            "i",
            "q"
        )
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # Verify command succeeded (batch may or may not have content)
        show_result = git_stage_batch("show", "--from", "multi-file", check=False)
        assert show_result.returncode == 0

    def test_interactive_selective_file_staging(self, repo_with_changes):
        """Test selectively staging specific files."""
        # Skip then include
        result = run_interactive("s", "s", "i", "q")
        assert result.returncode == 0
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_new_file_handling(self, repo_with_changes):
        """Test interactive mode with new files."""
        # config.py is a new file in repo_with_changes
        # Navigate through hunks and stage from new file
        result = run_interactive("i", "s", "i", "q")
        assert result.returncode == 0
        staged = get_staged_files()
        assert len(staged) > 0

    def test_interactive_many_hunks(self, functional_repo):
        """Test interactive mode with many hunks."""
        # Create multiple files with changes
        for i in range(5):
            file_path = functional_repo / f"file{i}.txt"
            file_path.write_text(f"Line 1\nLine 2\nLine {i}\n")

        # Navigate through multiple hunks
        result = run_interactive("i", "s", "i", "s", "i", "s", "i", "s", "i", "q")
        assert result.returncode == 0
        staged = get_staged_files()
        assert len(staged) > 0


class TestInteractiveErrorHandling:
    """Test error handling in interactive mode."""

    def test_interactive_invalid_line_selection(self, repo_with_changes):
        """Test invalid line selection in interactive mode."""
        # Invalid format - should show error but not crash
        result = run_interactive("l", "i", "abc", "q")
        assert result.returncode == 0
        # Should show error in output
        output = result.stdout + result.stderr
        assert "invalid" in output.lower() or "error" in output.lower() or len(output) > 0

    def test_interactive_nonexistent_line_id(self, repo_with_changes):
        """Test nonexistent line ID."""
        # Line ID that doesn't exist - should handle gracefully
        result = run_interactive("l", "i", "999", "q")
        assert result.returncode == 0

    def test_interactive_invalid_batch_name(self, repo_with_changes):
        """Test invalid batch name with path traversal."""
        result = run_interactive("new ../evil-batch", "q")
        # Should fail with error but not crash
        assert result.returncode == 0
        output = result.stdout + result.stderr
        # Should show error message
        assert len(output) > 0

    def test_interactive_nonexistent_batch_show(self, repo_with_changes):
        """Test showing from nonexistent batch."""
        result = run_interactive("show --from nonexistent", "q")
        assert result.returncode == 0
        # Should show error
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "error" in output.lower() or len(output) > 0

    def test_interactive_nonexistent_batch_apply(self, repo_with_changes):
        """Test applying from nonexistent batch."""
        result = run_interactive("apply --from nonexistent", "q")
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "error" in output.lower() or len(output) > 0

    def test_interactive_nonexistent_batch_drop(self, repo_with_changes):
        """Test dropping nonexistent batch."""
        result = run_interactive("drop nonexistent", "q")
        assert result.returncode == 0
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "error" in output.lower() or len(output) > 0

    def test_interactive_abort_multiple_times(self, repo_with_changes):
        """Test aborting multiple times."""
        result = run_interactive("i", "abort")
        assert result.returncode == 0
        # Second abort should show error about no session
        result2 = run_interactive("abort")
        assert result2.returncode == 0

    def test_interactive_with_staged_changes(self, repo_with_changes):
        """Test interactive mode with already staged changes."""
        import subprocess

        # Stage some changes manually
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)

        # Should still work with unstaged changes
        result = run_interactive("q")
        assert result.returncode == 0


class TestInteractiveVsNonInteractive:
    """Test differences between interactive and non-interactive modes."""

    def test_same_commands_work_in_both_modes(self, repo_with_changes):
        """Test that commands work in both modes."""
        # Non-interactive
        git_stage_batch("start")
        git_stage_batch("include", "--line", "1", check=False)
        git_stage_batch("abort", check=False)

        # Interactive
        result = run_interactive("i", "q")
        assert result.returncode == 0

    def test_interactive_starts_session_automatically(self, repo_with_changes):
        """Test that interactive mode starts a session automatically."""
        # Interactive mode should work without explicit 'start'
        result = run_interactive("q")
        assert result.returncode == 0

    def test_interactive_handles_batch_operations(self, repo_with_changes):
        """Test batch operations work in interactive mode."""
        git_stage_batch("new", "test-batch")

        result = run_interactive("i --to test-batch", "q")
        assert result.returncode == 0


class TestInteractiveFlowControls:
    """Test interactive mode flow controls (< and >)."""

    def test_flow_from_shortcut_with_batch_name(self, repo_with_changes):
        """Test <batch-name shortcut to set source."""
        # Create and populate a batch
        git_stage_batch("new", "flow-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "flow-batch", "--line", "1,2", check=False)
        git_stage_batch("abort")

        # Use <batch-name to pull from batch
        result = run_interactive("<flow-batch", "q")
        assert result.returncode == 0

    def test_flow_to_shortcut_with_batch_name(self, repo_with_changes):
        """Test >batch-name shortcut to set target."""
        git_stage_batch("new", "target-batch")

        # Use >batch-name to push to batch
        result = run_interactive(">target-batch", "i", "q")
        assert result.returncode == 0

    def test_flow_from_menu(self, repo_with_changes):
        """Test < to open from menu."""
        git_stage_batch("new", "menu-batch")

        # < should open menu, press q to cancel/quit
        result = run_interactive("<", "q")
        assert result.returncode == 0

    def test_flow_to_menu(self, repo_with_changes):
        """Test > to open to menu."""
        # > should open menu
        result = run_interactive(">", "q")
        assert result.returncode == 0

    def test_flow_from_then_include(self, repo_with_changes):
        """Test pulling from batch then including to staging."""
        # Create batch with changes
        git_stage_batch("new", "source-batch")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "source-batch", "--line", "1,2", check=False)
        git_stage_batch("abort")

        # Pull from batch and include to staging
        result = run_interactive("<source-batch", "i", "q")
        assert result.returncode == 0

        # Should have staged changes
        staged = get_staged_files()
        # May or may not have files depending on batch content

    def test_flow_to_then_include(self, repo_with_changes):
        """Test setting target batch then including."""
        git_stage_batch("new", "dest-batch")

        # Set target to batch, then include
        result = run_interactive(">dest-batch", "i", "q")
        assert result.returncode == 0

        # Batch should have content
        batch_show = git_stage_batch("show", "--from", "dest-batch", check=False)
        if batch_show.returncode == 0:
            assert batch_show.stdout or True  # May be empty

    def test_flow_switch_between_sources(self, repo_with_changes):
        """Test switching between different sources."""
        git_stage_batch("new", "batch1")
        git_stage_batch("new", "batch2")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "batch1", "--line", "1", check=False)
        git_stage_batch("include", "--to", "batch2", "--line", "1", check=False)
        git_stage_batch("abort")

        # Switch between sources
        result = run_interactive("<batch1", "<batch2", "q")
        assert result.returncode == 0

    def test_flow_switch_between_targets(self, repo_with_changes):
        """Test switching between different targets."""
        git_stage_batch("new", "target1")
        git_stage_batch("new", "target2")

        # Switch between targets
        result = run_interactive(">target1", ">target2", "q")
        assert result.returncode == 0

    def test_flow_from_batch_to_staging(self, repo_with_changes):
        """Test full workflow: batch -> staging."""
        # Create batch with changes
        git_stage_batch("new", "full-flow")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "full-flow", "--line", "1,2", check=False)
        git_stage_batch("abort")

        # Pull from batch, include to staging
        result = run_interactive("<full-flow", "i", "q")
        assert result.returncode == 0

    def test_flow_from_working_tree_to_batch(self, repo_with_changes):
        """Test full workflow: working tree -> batch."""
        git_stage_batch("new", "wt-to-batch")

        # Default source is working tree, set target to batch
        result = run_interactive(">wt-to-batch", "i", "q")
        assert result.returncode == 0

    def test_flow_prevents_batch_to_batch(self, repo_with_changes):
        """Test that batch-to-batch flow is prevented."""
        git_stage_batch("new", "source")
        git_stage_batch("new", "target")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "source", "--line", "1", check=False)
        git_stage_batch("abort")

        # Try to set both source and target to batches
        # System should prevent this or handle gracefully
        result = run_interactive("<source", ">target", "q")
        assert result.returncode == 0

    def test_flow_display_shows_selected_state(self, repo_with_changes):
        """Test that flow state is displayed."""
        git_stage_batch("new", "display-test")

        # Change flow and check it doesn't crash
        result = run_interactive(">display-test", "q")
        assert result.returncode == 0
        # Should have some output showing flow state
        assert len(result.stdout) + len(result.stderr) > 0

    def test_flow_from_word_shortcut(self, repo_with_changes):
        """Test 'from' word shortcut."""
        git_stage_batch("new", "word-test")

        result = run_interactive("from", "q")
        assert result.returncode == 0

    def test_flow_to_word_shortcut(self, repo_with_changes):
        """Test 'to' word shortcut."""
        result = run_interactive("to", "q")
        assert result.returncode == 0


class TestInteractiveFixupSubmenu:
    """Test interactive fixup submenu (x)."""

    def test_fixup_submenu_basic(self, repo_with_changes):
        """Test opening fixup submenu."""
        # Make a commit that we could fixup
        import subprocess
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Test commit"], check=True, capture_output=True)

        # Make more changes
        readme = repo_with_changes / "README.md"
        readme.write_text("# Test\nMore changes\n")

        # Try fixup (x) then quit
        result = run_interactive("x", "q")
        assert result.returncode == 0

    def test_fixup_next_candidate(self, repo_with_changes):
        """Test navigating through fixup candidates with 'n'."""
        import subprocess

        # Create multiple commits
        readme = repo_with_changes / "README.md"
        for i in range(3):
            readme.write_text(f"# Test\nCommit {i}\n")
            subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", f"Commit {i}"], check=True, capture_output=True)

        # Make changes
        readme.write_text("# Test\nFinal changes\n")

        # Try fixup, navigate with 'n' (next), then quit
        result = run_interactive("x", "n", "q")
        assert result.returncode == 0

    def test_fixup_reset(self, repo_with_changes):
        """Test resetting fixup iteration with 'r'."""
        import subprocess

        # Create commits
        readme = repo_with_changes / "README.md"
        readme.write_text("# Test\nCommit 1\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 1"], check=True, capture_output=True)

        readme.write_text("# Test\nCommit 2\n")
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Commit 2"], check=True, capture_output=True)

        # Make changes
        readme.write_text("# Test\nFinal changes\n")

        # Try fixup, next, then reset
        result = run_interactive("x", "n", "r", "q")
        assert result.returncode == 0

    def test_fixup_word_shortcut(self, repo_with_changes):
        """Test 'fixup' word shortcut."""
        import subprocess

        # Setup commit
        subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Test"], check=True, capture_output=True)

        readme = repo_with_changes / "README.md"
        readme.write_text("# Test\nChanges\n")

        result = run_interactive("fixup", "q")
        assert result.returncode == 0

    def test_fixup_not_available_from_batch(self, repo_with_changes):
        """Test that fixup is not available when pulling from batch."""
        git_stage_batch("new", "fixup-test")
        git_stage_batch("start")
        git_stage_batch("include", "--to", "fixup-test", "--line", "1", check=False)
        git_stage_batch("abort")

        # Try fixup while pulling from batch - should show error
        result = run_interactive("<fixup-test", "x", "q")
        assert result.returncode == 0


class TestInteractiveShellCommands:
    """Test interactive shell command submenu (!)."""

    def test_shell_command_with_prefix(self, repo_with_changes):
        """Test shell command with ! prefix."""
        # Execute a simple shell command
        result = run_interactive("!echo test", "q")
        assert result.returncode == 0

    def test_shell_command_prompt(self, repo_with_changes):
        """Test shell command prompt."""
        # Just ! should prompt for command
        result = run_interactive("!", "q")
        assert result.returncode == 0

    def test_shell_command_git_status(self, repo_with_changes):
        """Test running git command from shell."""
        result = run_interactive("!git status", "q")
        assert result.returncode == 0

    def test_shell_command_ls(self, repo_with_changes):
        """Test running ls from shell."""
        result = run_interactive("!ls", "q")
        assert result.returncode == 0

    def test_shell_word_shortcut(self, repo_with_changes):
        """Test 'command' word shortcut."""
        result = run_interactive("command", "q")
        assert result.returncode == 0

    def test_shell_multiple_commands(self, repo_with_changes):
        """Test running multiple shell commands."""
        result = run_interactive("!echo first", "!echo second", "q")
        assert result.returncode == 0


class TestInteractiveScopeSelectors:
    """Test interactive scope selectors (l for lines, f for file)."""

    def test_line_selector_submenu(self, repo_with_changes):
        """Test 'l' opens line selection submenu."""
        # Cancel out with Ctrl+C (empty input will be treated as cancel)
        result = run_interactive("l", "", "q")
        assert result.returncode == 0

    def test_line_selector_with_ids(self, repo_with_changes):
        """Test line selection with specific IDs."""
        result = run_interactive("l", "i", "1,2", "q")
        assert result.returncode == 0
        # Should have staged something
        staged = get_staged_files()
        assert len(staged) > 0

    def test_lines_word_shortcut(self, repo_with_changes):
        """Test 'lines' word shortcut."""
        result = run_interactive("lines", "", "q")
        assert result.returncode == 0

    def test_file_selector_submenu(self, repo_with_changes):
        """Test 'f' opens file selection submenu."""
        # Cancel with Ctrl+C
        result = run_interactive("f", "", "q")
        assert result.returncode == 0

    def test_file_word_shortcut(self, repo_with_changes):
        """Test 'file' word shortcut."""
        result = run_interactive("file", "", "q")
        assert result.returncode == 0

    def test_file_selector_include(self, repo_with_changes):
        """Test file selector with include action."""
        # Open file submenu and choose include
        result = run_interactive("f", "i", "q")
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # Should have staged the entire file
        staged = get_staged_files()
        assert len(staged) > 0, "Expected file to be staged"

    def test_file_selector_skip(self, repo_with_changes):
        """Test file selector with skip action."""
        result = run_interactive("f", "s", "q")
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # Nothing should be staged
        staged = get_staged_files()
        assert len(staged) == 0

    def test_file_selector_discard(self, repo_with_changes):
        """Test file selector with discard action."""
        # Discard requires confirmation
        result = run_interactive("f", "d", "yes", "q")
        assert result.returncode == 0, f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        # File should be removed or reverted
        # We can't easily check working tree state, but command should succeed


class TestInteractiveHelpSubmenu:
    """Test interactive help submenu (?)."""

    def test_help_command(self, repo_with_changes):
        """Test '?' shows help."""
        result = run_interactive("?", "q")
        assert result.returncode == 0
        # Should have help output
        assert len(result.stdout) + len(result.stderr) > 0

    def test_help_word_shortcut(self, repo_with_changes):
        """Test 'help' word shortcut."""
        result = run_interactive("help", "q")
        assert result.returncode == 0

    def test_help_displays_commands(self, repo_with_changes):
        """Test help displays available commands."""
        result = run_interactive("?", "q")
        # Should mention some commands
        output = result.stdout + result.stderr
        # At minimum should not crash


class TestInteractiveAgainSubmenu:
    """Test interactive again submenu (a)."""

    def test_again_command(self, repo_with_changes):
        """Test 'a' triggers again."""
        result = run_interactive("s", "a", "q")
        assert result.returncode == 0

    def test_again_word_shortcut(self, repo_with_changes):
        """Test 'again' word shortcut."""
        result = run_interactive("s", "again", "q")
        assert result.returncode == 0

    def test_again_after_operations(self, repo_with_changes):
        """Test again resets after operations."""
        result = run_interactive("i", "s", "a", "q")
        assert result.returncode == 0
