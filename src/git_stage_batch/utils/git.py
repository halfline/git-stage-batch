"""Git command execution utilities."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
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


def stream_git_command(arguments: list[str]) -> Iterator[str]:
    """Stream git command output line-by-line.

    If the caller stops consuming early, the git process is terminated
    and no error is raised for that intentional cancellation.

    Args:
        arguments: Git command arguments (e.g., ["diff", "--no-color"])

    Yields:
        Lines from git's stdout

    Raises:
        subprocess.CalledProcessError: If git command fails
    """
    process = subprocess.Popen(
        ["git", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    cancelled = False

    assert process.stdout is not None
    assert process.stderr is not None

    try:
        for line in process.stdout:
            yield line
    except GeneratorExit:
        cancelled = True

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    finally:
        process.stdout.close()

        if process.poll() is None:
            process.wait()

        if not cancelled and process.returncode != 0:
            stderr = process.stderr.read()
            raise subprocess.CalledProcessError(
                process.returncode,
                ["git", *arguments],
                stderr=stderr,
            )

        process.stderr.close()


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
