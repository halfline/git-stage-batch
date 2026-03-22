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


def resolve_file_path_to_repo_relative(file_path: str) -> str:
    """Convert a file path to repository-relative format.

    Args:
        file_path: File path to convert

    Returns:
        Repository-relative path, or original path if outside repo
    """
    repo_root = get_git_repository_root_path()
    path = Path(file_path)

    # If it's already relative, use it as-is
    if not path.is_absolute():
        return file_path

    # If it's absolute, make it relative to repo root
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        # Path is outside repo, return as-is
        return file_path


def get_gitignore_path() -> Path:
    """Get the path to the repository's .gitignore file.

    Returns:
        Path to .gitignore
    """
    return get_git_repository_root_path() / ".gitignore"


def read_gitignore_lines() -> list[str]:
    """Read .gitignore file, returning lines preserving original formatting.

    Returns:
        List of lines from .gitignore with original formatting
    """
    from .file_io import read_text_file_contents

    gitignore_path = get_gitignore_path()
    if not gitignore_path.exists():
        return []
    content = read_text_file_contents(gitignore_path)
    # Preserve exact formatting including trailing newline
    return content.splitlines(keepends=True)


def write_gitignore_lines(lines: list[str]) -> None:
    """Write lines to .gitignore, preserving formatting.

    Args:
        lines: Lines to write to .gitignore
    """
    from .file_io import write_text_file_contents

    gitignore_path = get_gitignore_path()
    content = "".join(lines)
    write_text_file_contents(gitignore_path, content)


def add_file_to_gitignore(file_path: str) -> None:
    """Add a file path to .gitignore.

    Args:
        file_path: File path to add
    """
    lines = read_gitignore_lines()

    # Check if already present
    file_path_normalized = file_path.rstrip("\n")
    for line in lines:
        if line.rstrip("\n") == file_path_normalized:
            return  # Already present

    # Add to end
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    lines.append(f"{file_path}\n")

    write_gitignore_lines(lines)


def remove_file_from_gitignore(file_path: str) -> bool:
    """Remove a file path from .gitignore.

    Args:
        file_path: File path to remove

    Returns:
        True if removed, False if not found
    """
    lines = read_gitignore_lines()
    file_path_normalized = file_path.rstrip("\n")

    i = 0
    removed = False
    while i < len(lines):
        if lines[i].rstrip("\n") == file_path_normalized:
            # Remove the path
            del lines[i]
            removed = True
            continue  # Don't increment i, check same position again
        i += 1

    if removed:
        write_gitignore_lines(lines)

    return removed
