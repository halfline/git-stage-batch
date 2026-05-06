"""POSIX-oriented subprocess streaming with bounded memory usage.

This module streams stdin, stdout, stderr, and additional child file
descriptors. The core is binary-only and chunk-based.

Example usage:

    # One-shot streaming with stdin
    for event in stream_command(["cat"], stdin_chunks=[b"hello\n"]):
        if isinstance(event, OutputEvent):
            print(f"fd {event.fd}: {event.data!r}")
        elif isinstance(event, ExitEvent):
            print(f"exit code: {event.exit_code}")

    # Capture extra child fd (e.g., Xvfb -displayfd 3)
    for event in stream_command(
        ["python3", "-c", "import os; os.write(3, b'display:1\\n'); os.close(3)"],
        extra_fds=[CapturedFd(3)],
    ):
        if isinstance(event, OutputEvent) and event.fd == 3:
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
import selectors
import signal
import subprocess
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Literal


_CHUNK_SIZE = 8192


class _SpawnedProcess:
    """Minimal Popen-like wrapper around a posix_spawn pid."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def poll(self) -> int | None:
        """Check if process has terminated, return exit code if so."""
        if self.returncode is not None:
            return self.returncode

        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            # Already reaped
            return 0

        if pid == 0:
            # Still running
            return None

        if os.WIFEXITED(status):
            self.returncode = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self.returncode = -os.WTERMSIG(status)
        else:
            self.returncode = 0

        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Wait for process to terminate and return exit code."""
        if self.returncode is not None:
            return self.returncode

        if timeout is None:
            pid, status = os.waitpid(self.pid, 0)
        else:
            # Implement timeout via polling
            deadline = time.time() + timeout
            while True:
                result = self.poll()
                if result is not None:
                    return result
                if time.time() >= deadline:
                    raise subprocess.TimeoutExpired(str(self.pid), timeout)
                time.sleep(0.01)

        if os.WIFEXITED(status):
            self.returncode = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self.returncode = -os.WTERMSIG(status)
        else:
            self.returncode = 0

        return self.returncode

    def terminate(self) -> None:
        """Send SIGTERM to the process."""
        if self.poll() is None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def kill(self) -> None:
        """Send SIGKILL to the process."""
        if self.poll() is None:
            try:
                os.kill(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class CommandEventRole(Enum):
    """Command event role/type."""

    OUTPUT = "output"
    STDIN_CLOSED = "stdin_closed"
    EXIT = "exit"


@dataclass
class OutputEvent:
    """Represents output from a child file descriptor."""

    role: Literal[CommandEventRole.OUTPUT]
    fd: int
    data: bytes


@dataclass
class StdinClosedEvent:
    """Represents parent closing child stdin."""

    role: Literal[CommandEventRole.STDIN_CLOSED]


@dataclass
class ExitEvent:
    """Represents child process exit."""

    role: Literal[CommandEventRole.EXIT]
    exit_code: int


CommandEvent = OutputEvent | StdinClosedEvent | ExitEvent


@dataclass(frozen=True)
class CapturedFd:
    """Specification for capturing an extra child file descriptor."""

    child_fd: int


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


class StreamingProcess:
    """A running subprocess with streaming I/O.

    Do not construct directly; use start_command() instead.
    """

    def __init__(
        self,
        process: _SpawnedProcess,
        stdin_fd: int | None,
        output_fds: dict[int, int],
    ):
        self._process = process
        self._stdin_fd = stdin_fd
        self._output_fds = output_fds

        self._stdin_selected_chunk: bytes | None = None
        self._stdin_chunk_offset = 0
        self._stdin_iter: Iterator[bytes] | None = None
        self._stdin_close_requested = False
        self._stdin_closed_emitted = False

        self._selector: selectors.BaseSelector | None = None
        self._events_started = False
        self._cleaned_up = False

    def terminate(self) -> None:
        """Send SIGTERM to the child if still running."""
        if self._process.poll() is None:
            self._process.terminate()

    def kill(self) -> None:
        """Send SIGKILL to the child if still running."""
        if self._process.poll() is None:
            self._process.kill()

    def wait(self) -> int:
        """Wait for process exit and return exit code.

        If stream() has not started, captured output is drained and
        discarded so pipe resources are closed.
        """
        if self._events_started:
            try:
                return self._process.wait()
            finally:
                self._close_resources()

        stdin_chunks: Iterable[bytes] | None = None
        if self._stdin_fd is not None:
            stdin_chunks = ()

        exit_code = 0
        for event in self.stream(stdin_chunks):
            if isinstance(event, ExitEvent):
                exit_code = event.exit_code
        return exit_code

    def stream(self, stdin_chunks: Iterable[bytes] | None = None) -> Iterator[CommandEvent]:
        """Stream I/O with the child process.

        Iteration continues until all captured output file descriptors reach EOF
        and any managed stdin pipe has been closed. After that, the child is
        waited for and a final ExitEvent is emitted.

        Args:
            stdin_chunks: Optional iterator of bytes to write to stdin. If provided,
                chunks are written as the child's stdin becomes ready, with no
                in-memory buffering beyond the kernel's pipe buffer.

        Yields:
            OutputEvent for each chunk from captured fds (stdout, stderr, extra_fds),
            optionally StdinClosedEvent when stdin is closed,
            and exactly one ExitEvent at the end with the process exit code.

        This is a single-consumer iterator. Do not call multiple times.

        Iteration completes based on output fd closure, not process exit.
        If a child or descendant keeps a captured fd open (e.g., a backgrounded
        grandchild inherits stdout), iteration will wait indefinitely for that fd
        to close. Ensure child processes close all captured fds when done.
        """
        if self._events_started:
            raise RuntimeError("stream() can only be called once")
        self._events_started = True

        # Set up the stdin iterator if provided
        if stdin_chunks is not None:
            self._stdin_iter = iter(stdin_chunks)

        try:
            self._selector = selectors.DefaultSelector()

            for parent_fd in self._output_fds:
                self._selector.register(parent_fd, selectors.EVENT_READ)

            while self._output_fds or self._has_pending_stdin():
                self._update_stdin_registration()

                if self._should_close_stdin_now():
                    self._close_stdin_now()
                    yield StdinClosedEvent(CommandEventRole.STDIN_CLOSED)
                    continue

                if not self._selector.get_map():
                    break

                events_list = self._selector.select()

                for key, mask in events_list:
                    fd_obj = key.fileobj
                    assert isinstance(fd_obj, int)

                    if mask & selectors.EVENT_READ:
                        yield from self._handle_readable_fd(fd_obj)

                    if mask & selectors.EVENT_WRITE:
                        maybe_event = self._handle_writable_stdin(fd_obj)
                        if maybe_event is not None:
                            yield maybe_event

            # All outputs drained, wait for child and emit exit event
            exit_code = self._process.wait()
            yield ExitEvent(CommandEventRole.EXIT, exit_code)

        except GeneratorExit:
            # Iterator closed early - terminate the child
            _terminate_then_kill(self)
            raise
        finally:
            self._close_resources()

    def _has_pending_stdin(self) -> bool:
        return (
            self._stdin_fd is not None
            and not self._stdin_closed_emitted
            and (
                self._stdin_selected_chunk is not None
                or self._stdin_iter is not None
                or self._stdin_close_requested
            )
        )

    def _update_stdin_registration(self) -> None:
        if self._selector is None or self._stdin_fd is None:
            return

        try:
            self._selector.get_key(self._stdin_fd)
            is_registered = True
        except KeyError:
            is_registered = False

        has_data = self._stdin_selected_chunk is not None or self._stdin_iter is not None

        if has_data and not self._stdin_close_requested:
            if not is_registered:
                self._selector.register(self._stdin_fd, selectors.EVENT_WRITE)
        else:
            if is_registered:
                self._selector.unregister(self._stdin_fd)

    def _should_close_stdin_now(self) -> bool:
        return (
            self._stdin_fd is not None
            and self._stdin_close_requested
            and self._stdin_selected_chunk is None
            and self._stdin_iter is None
            and not self._stdin_closed_emitted
        )

    def _close_stdin_now(self) -> None:
        assert self._stdin_fd is not None

        if self._selector is not None:
            try:
                self._selector.unregister(self._stdin_fd)
            except KeyError:
                pass

        try:
            os.close(self._stdin_fd)
        except OSError:
            pass

        self._stdin_fd = None
        self._stdin_closed_emitted = True

    def _handle_readable_fd(self, parent_fd: int) -> Iterator[CommandEvent]:
        try:
            data = os.read(parent_fd, _CHUNK_SIZE)
        except OSError:
            data = b""

        if data:
            child_fd = self._output_fds[parent_fd]
            yield OutputEvent(CommandEventRole.OUTPUT, child_fd, data)
            return

        # EOF on this fd - close and remove from monitoring
        if self._selector is not None:
            try:
                self._selector.unregister(parent_fd)
            except KeyError:
                pass

        try:
            os.close(parent_fd)
        except OSError:
            pass

        self._output_fds.pop(parent_fd, None)

    def _handle_writable_stdin(self, parent_fd: int) -> StdinClosedEvent | None:
        if self._stdin_fd is None or parent_fd != self._stdin_fd:
            return None

        # Get next chunk if needed, skipping empty chunks
        while self._stdin_selected_chunk is None and self._stdin_iter is not None:
            try:
                chunk = next(self._stdin_iter)
                if chunk:  # Skip empty chunks
                    self._stdin_selected_chunk = chunk
                    self._stdin_chunk_offset = 0
            except StopIteration:
                self._stdin_iter = None
                self._stdin_close_requested = True
                break

        if self._stdin_selected_chunk is None:
            return None

        # Write what we can
        view = self._stdin_selected_chunk[self._stdin_chunk_offset:]

        try:
            written = os.write(self._stdin_fd, view)
        except OSError as e:
            # Broken pipe / connection reset is expected when child closes stdin early
            if e.errno in (errno.EPIPE, errno.ECONNRESET):
                written = 0
            else:
                # Unexpected write error - propagate it
                raise

        if written > 0:
            self._stdin_chunk_offset += written
            if self._stdin_chunk_offset >= len(self._stdin_selected_chunk):
                # Done with this chunk
                self._stdin_selected_chunk = None
                self._stdin_chunk_offset = 0
        else:
            # Broken pipe - child closed stdin, discard remaining input
            self._stdin_selected_chunk = None
            self._stdin_chunk_offset = 0
            self._stdin_iter = None
            self._stdin_close_requested = True

        if self._should_close_stdin_now():
            self._close_stdin_now()
            return StdinClosedEvent(CommandEventRole.STDIN_CLOSED)

        return None

    def _close_resources(self) -> None:
        """Close local file descriptors and selector.

        This closes parent-side resources but does not guarantee process
        termination. If the child is still running, it may continue running
        as a background process. Callers should terminate() the child first
        if they want to ensure it exits.
        """
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self._selector is not None:
            try:
                self._selector.close()
            except Exception:
                pass
            self._selector = None

        # Close stdin fd
        if self._stdin_fd is not None:
            try:
                os.close(self._stdin_fd)
            except OSError:
                pass
            self._stdin_fd = None

        # Close all output fds
        for parent_fd in list(self._output_fds):
            try:
                os.close(parent_fd)
            except OSError:
                pass
        self._output_fds.clear()

        # Attempt to reap the child if it has already exited
        # (If stream() completed normally, this is already done)
        self._process.poll()


def start_command(
    arguments: list[str],
    *,
    stdin: bool = False,
    stdin_fd: int | None = None,
    extra_fds: list[CapturedFd] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> StreamingProcess:
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

    process = _SpawnedProcess(pid)
    return StreamingProcess(process, stdin_write_fd, output_fds)


def _terminate_then_kill(
    process: StreamingProcess,
    *,
    terminate_timeout: float = 0.5,
) -> None:
    process.terminate()
    try:
        process._process.wait(timeout=terminate_timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process._process.wait()


def stream_command(
    arguments: list[str],
    stdin_chunks: Iterable[bytes] | None = None,
    *,
    stdin_fd: int | None = None,
    extra_fds: list[CapturedFd] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_stdout: bool = True,
    capture_stderr: bool = True,
) -> Iterator[CommandEvent]:
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
        _terminate_then_kill(proc)
        raise
    finally:
        proc._close_resources()


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
        if isinstance(event, ExitEvent):
            returncode = event.exit_code
        elif isinstance(event, OutputEvent):
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
