"""Git command execution utilities."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..exceptions import exit_with_error
from ..i18n import _


def run_git_command(
    arguments: list[str],
    check: bool = True,
    text_output: bool = True
) -> subprocess.CompletedProcess:
    """Execute a git command with error handling.

    Args:
        arguments: Git command arguments (e.g., ["status", "--short"])
        check: Whether to raise CalledProcessError on non-zero exit
        text_output: Whether to decode stdout/stderr as text

    Returns:
        CompletedProcess with returncode, stdout, stderr

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    return subprocess.run(
        ["git", *arguments],
        check=check,
        text=text_output,
        capture_output=True
    )


def require_git_repository() -> None:
    """Verify that we are inside a git repository.

    Calls exit_with_error if not in a git repository, printing git's
    error message for context.

    Raises:
        SystemExit: Via exit_with_error if not in a git repository
    """
    try:
        run_git_command(["rev-parse", "--git-dir"])
    except subprocess.CalledProcessError as error:
        # Print git's actual error message which contains helpful context
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        exit_with_error(_("Not inside a git repository."), exit_code=error.returncode)


def get_git_repository_root_path() -> Path:
    """Get the absolute path to the git repository root.

    Returns:
        Path object pointing to the repository root directory

    Raises:
        subprocess.CalledProcessError: If not in a git repository
    """
    output = run_git_command(["rev-parse", "--show-toplevel"]).stdout.strip()
    return Path(output)
