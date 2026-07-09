"""POSIX-oriented subprocess streaming with bounded memory usage.

This module streams stdin, stdout, stderr, and additional child file
descriptors. The core is binary-only and chunk-based.

Example usage:

    # One-shot streaming with stdin
    from git_stage_batch.utils import command_events

    for event in stream_command(["cat"], stdin_chunks=[b"hello\n"]):
        if isinstance(event, command_events.OutputEvent):
            print(f"fd {event.fd}: {event.data!r}")
        elif isinstance(event, command_events.ExitEvent):
            print(f"exit code: {event.exit_code}")

    # Capture extra child fd (e.g., Xvfb -displayfd 3)
    for event in stream_command(
        ["python3", "-c", "import os; os.write(3, b'display:1\\n'); os.close(3)"],
        extra_fds=[command_events.CapturedFd(3)],
    ):
        if isinstance(event, command_events.OutputEvent) and event.fd == 3:
            display = event.data.decode().strip()

    # Process handle for interactive use
    proc = start_command(["cat"], stdin=True)
    for event in proc.stream():
        ...
"""

from __future__ import annotations

import errno
import locale
import os
import subprocess
from collections.abc import Iterable, Iterator

from . import command_events, command_streaming


def _close_fd_if_present(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _prepare_spawn_dup_source(
    source_fd: int,
    target_fds: set[int],
    cleanup_fds: list[int],
) -> int:
    if source_fd not in target_fds:
        return source_fd

    while True:
        duplicate_fd = os.dup(source_fd)
        cleanup_fds.append(duplicate_fd)
        if duplicate_fd not in target_fds:
            return duplicate_fd


def _add_spawn_dup2_action(
    file_actions: list[tuple[int, int] | tuple[int, int, int]],
    source_fd: int,
    target_fd: int,
    target_fds: set[int],
    cleanup_fds: list[int],
) -> None:
    spawn_source_fd = _prepare_spawn_dup_source(
        source_fd,
        target_fds,
        cleanup_fds,
    )
    file_actions.append((os.POSIX_SPAWN_DUP2, spawn_source_fd, target_fd))
    if spawn_source_fd != source_fd or source_fd not in target_fds:
        file_actions.append((os.POSIX_SPAWN_CLOSE, spawn_source_fd))


def _spawn_arguments_for_cwd(arguments: list[str], cwd: str | None) -> tuple[str, list[str]]:
    if cwd is None:
        return arguments[0], arguments

    shell = _resolve_spawn_executable_from_paths("sh", os.defpath.split(os.pathsep))
    shell_cwd = _cwd_argument_for_shell(cwd)
    script = 'cd "$1" || exit 127; shift; exec "$@"'
    return shell, [shell, "-c", script, "sh", shell_cwd, *arguments]


def _cwd_argument_for_shell(cwd: str) -> str:
    if cwd.startswith("-") and not os.path.isabs(cwd):
        return os.path.join(".", cwd)
    return cwd


def _close_fds(fds: Iterable[int | None]) -> None:
    for fd in fds:
        _close_fd_if_present(fd)


def _resolve_spawn_executable_from_paths(
    executable: str,
    paths: Iterable[str],
) -> str:
    if os.path.dirname(executable):
        return executable

    permission_denied = False
    for directory in paths:
        candidate = os.path.join(directory, executable) if directory else executable
        if os.path.isdir(candidate):
            permission_denied = True
            continue
        if os.access(candidate, os.X_OK):
            return candidate
        if os.path.exists(candidate):
            permission_denied = True

    if permission_denied:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), executable)
    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), executable)


def _resolve_spawn_executable(executable: str, env: dict[str, str]) -> str:
    return _resolve_spawn_executable_from_paths(executable, os.get_exec_path(env))


def _spawn_environment(env: dict[str, str] | None) -> dict[str, str]:
    spawn_env = os.environ.copy() if env is None else dict(env)
    spawn_env["PWD"] = os.getcwd()
    return spawn_env


def _add_spawn_close_action(
    file_actions: list[tuple[int, int] | tuple[int, int, int]],
    fd: int,
    target_fds: set[int],
) -> None:
    if fd not in target_fds:
        file_actions.append((os.POSIX_SPAWN_CLOSE, fd))


def start_command(
    arguments: list[str],
    *,
    stdin: bool = False,
    stdin_fd: int | None = None,
    extra_fds: list[command_events.CapturedFd] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> command_streaming.StreamingProcess:
    """Start a subprocess with streaming I/O using posix_spawn.

    Stdout and stderr are captured by default.
    """
    if not arguments:
        raise ValueError("arguments must not be empty")
    if extra_fds is None:
        extra_fds = []
    if stdin and stdin_fd is not None:
        raise ValueError("stdin and stdin_fd are mutually exclusive")

    child_fds_seen = {1, 2}
    for captured in extra_fds:
        if captured.child_fd < 3:
            raise ValueError(f"invalid child_fd: {captured.child_fd}")
        if captured.child_fd in child_fds_seen:
            raise ValueError(f"duplicate or reserved child_fd: {captured.child_fd}")
        child_fds_seen.add(captured.child_fd)

    executable, spawn_arguments = _spawn_arguments_for_cwd(arguments, cwd)
    spawn_env = _spawn_environment(env)
    executable_path = _resolve_spawn_executable(executable, spawn_env)

    # Create pipes for stdin/stdout/stderr
    if stdin:
        stdin_read_fd, stdin_write_fd = os.pipe()
    else:
        stdin_read_fd, stdin_write_fd = None, None

    if capture_stdout:
        stdout_read_fd, stdout_write_fd = os.pipe()
    else:
        stdout_read_fd, stdout_write_fd = None, None

    if capture_stderr:
        stderr_read_fd, stderr_write_fd = os.pipe()
    else:
        stderr_read_fd, stderr_write_fd = None, None

    # Create pipes for extra fds
    extra_pipes: dict[int, tuple[int, int]] = {}
    for captured in extra_fds:
        read_fd, write_fd = os.pipe()
        extra_pipes[captured.child_fd] = (read_fd, write_fd)

    cleanup_fds: list[int] = []
    file_actions: list[tuple[int, int] | tuple[int, int, int]] = []
    target_fds = set(extra_pipes)
    if stdin or stdin_fd is not None:
        target_fds.add(0)
    if capture_stdout:
        target_fds.add(1)
    if capture_stderr:
        target_fds.add(2)

    if stdin:
        assert stdin_read_fd is not None
        assert stdin_write_fd is not None
        _add_spawn_close_action(file_actions, stdin_write_fd, target_fds)
        _add_spawn_dup2_action(file_actions, stdin_read_fd, 0, target_fds, cleanup_fds)
    elif stdin_fd is not None:
        _add_spawn_dup2_action(file_actions, stdin_fd, 0, target_fds, cleanup_fds)

    if capture_stdout:
        assert stdout_read_fd is not None
        assert stdout_write_fd is not None
        _add_spawn_close_action(file_actions, stdout_read_fd, target_fds)
        _add_spawn_dup2_action(file_actions, stdout_write_fd, 1, target_fds, cleanup_fds)

    if capture_stderr:
        assert stderr_read_fd is not None
        assert stderr_write_fd is not None
        _add_spawn_close_action(file_actions, stderr_read_fd, target_fds)
        _add_spawn_dup2_action(file_actions, stderr_write_fd, 2, target_fds, cleanup_fds)

    for child_fd, (read_fd, write_fd) in extra_pipes.items():
        _add_spawn_close_action(file_actions, read_fd, target_fds)
        _add_spawn_dup2_action(file_actions, write_fd, child_fd, target_fds, cleanup_fds)

    try:
        pid = os.posix_spawn(
            executable_path,
            spawn_arguments,
            spawn_env,
            file_actions=file_actions,
        )
    except Exception:
        _close_fds([
            stdin_read_fd,
            stdin_write_fd,
            stdout_read_fd,
            stdout_write_fd,
            stderr_read_fd,
            stderr_write_fd,
            *cleanup_fds,
        ])
        for pipe_fds in extra_pipes.values():
            _close_fds(pipe_fds)
        raise

    # Parent process
    # Close child-side fds
    if stdin:
        _close_fd_if_present(stdin_read_fd)
    elif stdin_fd is not None:
        _close_fd_if_present(stdin_fd)
    if capture_stdout:
        _close_fd_if_present(stdout_write_fd)
    if capture_stderr:
        _close_fd_if_present(stderr_write_fd)
    _close_fds(cleanup_fds)
    for _, write_fd in extra_pipes.values():
        _close_fd_if_present(write_fd)

    # Build fd maps
    output_fds: dict[int, int] = {}
    if capture_stdout and stdout_read_fd is not None:
        output_fds[stdout_read_fd] = 1
    if capture_stderr and stderr_read_fd is not None:
        output_fds[stderr_read_fd] = 2

    for child_fd, (read_fd, _) in extra_pipes.items():
        output_fds[read_fd] = child_fd

    process = command_streaming.SpawnedProcess(pid)
    return command_streaming.StreamingProcess(process, stdin_write_fd, output_fds)


def stream_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    stdin_fd: int | None = None,
    extra_fds: list[command_events.CapturedFd] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> Iterator[command_events.CommandEvent]:
    """One-shot convenience wrapper for streaming a command.

    This starts the command, optionally feeds stdin from an iterable, and
    yields events. If iteration is abandoned early, the child is terminated
    and cleaned up.

    Input and output are fully interleaved via select(): chunks are pulled from
    the iterator and written to stdin only when the fd is writable, with no
    in-memory buffering beyond the kernel's pipe buffer.
    """
    proc = start_command(
        arguments,
        stdin=stdin_chunks is not None,
        stdin_fd=stdin_fd,
        extra_fds=extra_fds,
        cwd=cwd,
        env=env,
        capture_stdout=capture_stdout,
        capture_stderr=capture_stderr,
    )

    try:
        yield from proc.stream(stdin_chunks=stdin_chunks)
    except GeneratorExit:
        command_streaming.terminate_then_kill(proc)
        raise
    finally:
        proc.close()


def run_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    check: bool = True,
    text_output: bool = True,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command to completion and capture stdout/stderr.

    This is the one-shot counterpart to stream_command(). It returns a
    subprocess.CompletedProcess-compatible object while using the same
    streaming implementation underneath.
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    returncode = 0

    for event in stream_command(
        arguments,
        stdin_chunks,
        cwd=cwd,
        env=env,
        capture_stdout=capture_stdout,
        capture_stderr=capture_stderr,
    ):
        if isinstance(event, command_events.ExitEvent):
            returncode = event.exit_code
        elif isinstance(event, command_events.OutputEvent):
            if event.fd == 1:
                stdout_chunks.append(event.data)
            elif event.fd == 2:
                stderr_chunks.append(event.data)

    stdout = b"".join(stdout_chunks) if capture_stdout else None
    stderr = b"".join(stderr_chunks) if capture_stderr else None

    if text_output:
        encoding = locale.getpreferredencoding(False)
        stdout = stdout.decode(encoding) if stdout is not None else None
        stderr = stderr.decode(encoding) if stderr is not None else None

    result = subprocess.CompletedProcess(
        arguments,
        returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode,
            arguments,
            output=stdout,
            stderr=stderr,
        )
    return result
