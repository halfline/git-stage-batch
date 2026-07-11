"""Git command execution utilities."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable, Iterator

from . import command_events, git_index_lock
from .command import run_command, stream_command
from .git_environment import git_environment_with_optional_locks_disabled
from ..core.text_lines import bytes_to_lines


def _prepare_git_command_environment(
    *,
    requires_index_lock: bool,
    cwd: str | None,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    if requires_index_lock:
        git_index_lock.wait_for_git_index_lock(cwd=cwd, env=env)
    return _git_command_environment(requires_index_lock=requires_index_lock, env=env)


def _git_command_environment(
    *,
    requires_index_lock: bool,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    if requires_index_lock:
        return env
    return git_environment_with_optional_locks_disabled(env)


def _index_lock_error_text(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def _is_git_index_lock_error(result: subprocess.CompletedProcess) -> bool:
    stderr_text = _index_lock_error_text(result.stderr).lower()
    return "index.lock" in stderr_text and (
        "file exists" in stderr_text or "unable to create" in stderr_text
    )


def _raise_git_command_error(result: subprocess.CompletedProcess) -> None:
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def _remaining_index_lock_wait_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def stream_git_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    requires_index_lock: bool = True,
) -> Iterator[bytes]:
    """Stream git command output as bytes lines.

    Wrapper around stream_command that:
    1. Prepends "git" to arguments
    2. Streams stdout as bytes lines (split at \\n)
    3. Captures stderr for error reporting
    4. Raises on non-zero exit with stderr in exception

    Args:
        arguments: Git command arguments (e.g., ["diff", "--no-color"])
        stdin_chunks: Iterable yielding bytes chunks to write to stdin (None for no input)
        cwd: Working directory for the command
        env: Environment variables
        requires_index_lock: Whether to wait for Git's index lock before running

    Yields:
        Bytes lines from stdout

    Raises:
        subprocess.CalledProcessError: If git command fails (includes stderr)
    """

    yield from bytes_to_lines(
        stream_git_command_bytes(
            arguments,
            stdin_chunks,
            cwd=cwd,
            env=env,
            requires_index_lock=requires_index_lock,
        )
    )


def stream_git_command_bytes(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    requires_index_lock: bool = True,
) -> Iterator[bytes]:
    """Stream raw Git stdout chunks without line-oriented buffering."""
    exit_code = 0
    stderr_chunks = []
    for event in stream_command(
        ["git", *arguments],
        stdin_chunks,
        cwd=cwd,
        env=_prepare_git_command_environment(
            requires_index_lock=requires_index_lock,
            cwd=cwd,
            env=env,
        ),
    ):
        if isinstance(event, command_events.ExitEvent):
            exit_code = event.exit_code
        elif isinstance(event, command_events.OutputEvent):
            if event.fd == 1:
                yield event.data
            elif event.fd == 2:
                stderr_chunks.append(event.data)

    if exit_code != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise subprocess.CalledProcessError(
            exit_code,
            ["git", *arguments],
            stderr=stderr_text,
        )


def stream_git_diff(
    *,
    base: str | None = None,
    target: str | None = None,
    cached: bool = False,
    context_lines: int | None = None,
    no_color: bool = True,
    full_index: bool = False,
    find_renames: bool = False,
    no_renames: bool = False,
    ignore_submodules: str | None = None,
    submodule_format: str | None = None,
    paths: Iterable[str] = (),
    stdin_chunks: Iterable[bytes] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Iterator[bytes]:
    """Stream a normalized, raw-content Git diff using keyworded options."""
    config_arguments = []
    arguments = [
        "--no-ext-diff",
        "--no-textconv",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--no-color",
    ]
    if ignore_submodules is not None:
        config_arguments.extend(["-c", f"diff.ignoreSubmodules={ignore_submodules}"])
        arguments.append(f"--ignore-submodules={ignore_submodules}")
    if submodule_format is not None:
        config_arguments.extend(["-c", f"diff.submodule={submodule_format}"])
        arguments.append(f"--submodule={submodule_format}")
    if full_index:
        arguments.append("--full-index")
    if find_renames and not no_renames:
        arguments.append("--find-renames")
    if no_renames:
        arguments.append("--no-renames")
    if cached:
        arguments.append("--cached")
    if context_lines is not None:
        arguments.append(f"-U{context_lines}")
    if base is not None:
        arguments.append(base)
    if target is not None:
        arguments.append(target)

    path_list = list(paths)
    if path_list:
        arguments.extend(["--", *path_list])

    return stream_git_command(
        [
            *config_arguments,
            "diff",
            *arguments,
        ],
        stdin_chunks,
        cwd=cwd,
        env=env,
        requires_index_lock=False,
    )


def run_git_command(
    arguments: list[str],
    check: bool = True,
    text_output: bool = True,
    *,
    stdin_chunks: Iterable[bytes] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
    requires_index_lock: bool = True,
) -> subprocess.CompletedProcess:
    """Execute a git command with error handling.

    Args:
        arguments: Git command arguments (e.g., ["status", "--short"])
        check: Whether to raise CalledProcessError on non-zero exit
        text_output: Whether to decode stdout/stderr as text
        stdin_chunks: Iterable yielding bytes chunks to write to stdin
        cwd: Working directory for the command
        env: Environment variables
        capture_stdout: Whether to capture stdout
        capture_stderr: Whether to capture stderr
        requires_index_lock: Whether to wait for Git's index lock before running

    Returns:
        CompletedProcess with returncode, stdout, stderr

    Raises:
        subprocess.CalledProcessError: If check=True and command fails
    """
    command = ["git", *arguments]
    reusable_stdin_chunks = (
        list(stdin_chunks)
        if requires_index_lock and stdin_chunks is not None
        else stdin_chunks
    )
    retry_deadline = (
        time.monotonic() + git_index_lock.DEFAULT_INDEX_LOCK_WAIT_SECONDS
        if requires_index_lock
        else None
    )

    if retry_deadline is not None:
        git_index_lock.wait_for_git_index_lock(
            cwd=cwd,
            env=env,
            timeout_seconds=_remaining_index_lock_wait_seconds(retry_deadline),
        )

    while True:
        result = run_command(
            command,
            reusable_stdin_chunks,
            check=False,
            text_output=text_output,
            cwd=cwd,
            env=_git_command_environment(
                requires_index_lock=requires_index_lock,
                env=env,
            ),
            capture_stdout=capture_stdout,
            capture_stderr=capture_stderr,
        )
        if not (
            retry_deadline is not None
            and result.returncode != 0
            and _is_git_index_lock_error(result)
        ):
            break

        remaining_seconds = _remaining_index_lock_wait_seconds(retry_deadline)
        if remaining_seconds <= 0:
            break
        git_index_lock.wait_for_git_index_lock(
            cwd=cwd,
            env=env,
            timeout_seconds=remaining_seconds,
        )

    if check and result.returncode != 0:
        _raise_git_command_error(result)
    return result
