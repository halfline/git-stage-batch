"""Tests for the TUI shell command escape."""

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from git_stage_batch.tui.shell_command import handle_shell_command


def test_handle_shell_command_runs_in_repository_root():
    """Test shell commands execute from the repository root."""
    repo_root = Path("/tmp/repo")

    with patch(
        "git_stage_batch.tui.shell_command.get_git_repository_root_path",
        return_value=repo_root,
    ):
        with patch(
            "git_stage_batch.tui.shell_command.subprocess.run",
            return_value=CompletedProcess(args="echo test", returncode=0),
        ) as mock_run:
            with patch("builtins.input", return_value=""):
                handle_shell_command("!echo test")

    mock_run.assert_called_once_with(
        "echo test",
        shell=True,
        cwd=repo_root,
    )
