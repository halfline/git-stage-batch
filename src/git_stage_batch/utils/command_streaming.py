"""Streaming subprocess process state."""

from __future__ import annotations

import errno
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Iterable, Iterator

from . import command_events


_CHUNK_SIZE = 8192


class SpawnedProcess:
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
        except ChildProcessError as error:
            raise ChildProcessError(
                f"Cannot determine exit status for child process {self.pid}"
            ) from error

        if pid == 0:
            return None

        if os.WIFEXITED(status):
            self.returncode = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            self.returncode = -os.WTERMSIG(status)
        else:
            raise ChildProcessError(
                f"Child process {self.pid} returned an unsupported wait status"
            )

        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Wait for process to terminate and return exit code."""
        if self.returncode is not None:
            return self.returncode

        if timeout is None:
            _pid, status = os.waitpid(self.pid, 0)
        else:
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
            raise ChildProcessError(
                f"Child process {self.pid} returned an unsupported wait status"
            )

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


class StreamingProcess:
    """A running subprocess with streaming I/O.

    Do not construct directly; use start_command() instead.
    """

    def __init__(
        self,
        process: SpawnedProcess,
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
            if isinstance(event, command_events.ExitEvent):
                exit_code = event.exit_code
        return exit_code

    def close(self) -> None:
        """Close parent-side resources held by the process handle."""
        self._close_resources()

    def stream(
        self,
        stdin_chunks: Iterable[bytes] | None = None,
    ) -> Iterator[command_events.CommandEvent]:
        """Stream I/O with the child process.

        Iteration continues until all captured output file descriptors reach EOF
        and any managed stdin pipe has been closed. After that, the child is
        waited for and a final exit event is emitted.

        Args:
            stdin_chunks: Optional iterator of bytes to write to stdin. If provided,
                chunks are written as the child's stdin becomes ready, with no
                in-memory buffering beyond the kernel's pipe buffer.

        Yields:
            Output events for each chunk from captured fds.
            Optionally a stdin-closed event when stdin is closed.
            Exactly one exit event at the end with the process exit code.

        This is a single-consumer iterator. Do not call multiple times.

        Iteration completes based on output fd closure, not process exit.
        If a child or descendant keeps a captured fd open (e.g., a backgrounded
        grandchild inherits stdout), iteration will wait indefinitely for that fd
        to close. Ensure child processes close all captured fds when done.
        """
        if self._events_started:
            raise RuntimeError("stream() can only be called once")
        self._events_started = True

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
                    yield command_events.StdinClosedEvent(
                        command_events.CommandEventRole.STDIN_CLOSED
                    )
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

            exit_code = self._process.wait()
            yield command_events.ExitEvent(
                command_events.CommandEventRole.EXIT,
                exit_code,
            )

        except GeneratorExit:
            terminate_then_kill(self)
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

        has_data = (
            self._stdin_selected_chunk is not None
            or self._stdin_iter is not None
        )

        if has_data and not self._stdin_close_requested:
            if not is_registered:
                self._selector.register(self._stdin_fd, selectors.EVENT_WRITE)
        elif is_registered:
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

    def _handle_readable_fd(
        self,
        parent_fd: int,
    ) -> Iterator[command_events.CommandEvent]:
        try:
            data = os.read(parent_fd, _CHUNK_SIZE)
        except OSError:
            data = b""

        if data:
            child_fd = self._output_fds[parent_fd]
            yield command_events.OutputEvent(
                command_events.CommandEventRole.OUTPUT,
                child_fd,
                data,
            )
            return

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

    def _handle_writable_stdin(
        self,
        parent_fd: int,
    ) -> command_events.StdinClosedEvent | None:
        if self._stdin_fd is None or parent_fd != self._stdin_fd:
            return None

        while self._stdin_selected_chunk is None and self._stdin_iter is not None:
            try:
                chunk = next(self._stdin_iter)
                if chunk:
                    self._stdin_selected_chunk = chunk
                    self._stdin_chunk_offset = 0
            except StopIteration:
                self._stdin_iter = None
                self._stdin_close_requested = True
                break

        if self._stdin_selected_chunk is None:
            return None

        view = self._stdin_selected_chunk[
            self._stdin_chunk_offset:self._stdin_chunk_offset + _CHUNK_SIZE
        ]

        try:
            written = os.write(self._stdin_fd, view)
        except OSError as e:
            if e.errno in (errno.EPIPE, errno.ECONNRESET):
                written = 0
            else:
                raise

        if written > 0:
            self._stdin_chunk_offset += written
            if self._stdin_chunk_offset >= len(self._stdin_selected_chunk):
                self._stdin_selected_chunk = None
                self._stdin_chunk_offset = 0
        else:
            self._stdin_selected_chunk = None
            self._stdin_chunk_offset = 0
            self._stdin_iter = None
            self._stdin_close_requested = True

        if self._should_close_stdin_now():
            self._close_stdin_now()
            return command_events.StdinClosedEvent(
                command_events.CommandEventRole.STDIN_CLOSED
            )

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

        if self._stdin_fd is not None:
            try:
                os.close(self._stdin_fd)
            except OSError:
                pass
            self._stdin_fd = None

        for parent_fd in list(self._output_fds):
            try:
                os.close(parent_fd)
            except OSError:
                pass
        self._output_fds.clear()

        self._process.poll()


def terminate_then_kill(
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
