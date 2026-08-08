"""Tests for POSIX subprocess streaming."""

import errno
import os
import subprocess
import time

import sys

import pytest

from git_stage_batch.utils import command as command_utils, command_streaming
from git_stage_batch.utils.command import (
    run_command,
    start_command,
    stream_command,
)
from git_stage_batch.utils.command_events import (
    CapturedFd,
    ExitEvent,
    OutputEvent,
    StdinClosedEvent,
)


class TestBasicStreaming:
    """Tests for basic stdout/stderr streaming."""

    def test_stdout_streaming(self):
        """Test streaming stdout from a simple command."""
        events = list(stream_command(["printf", "hello"]))

        # Should have output and exit events
        output_events = [e for e in events if isinstance(e, OutputEvent)]
        exit_events = [e for e in events if isinstance(e, ExitEvent)]

        assert len(exit_events) == 1
        assert exit_events[0].exit_code == 0

        # All output should be on fd 1 (stdout)
        assert all(e.fd == 1 for e in output_events)

        # Concatenate all output
        output_data = b"".join(e.data for e in output_events)
        assert output_data == b"hello"

    def test_stderr_streaming(self):
        """Test streaming stderr."""
        # Use Python to write to stderr
        events = list(stream_command([
            sys.executable, "-c",
            "import sys; sys.stderr.buffer.write(b'error\\n')"
        ]))

        output_events = [e for e in events if isinstance(e, OutputEvent)]
        stderr_events = [e for e in output_events if e.fd == 2]

        # Should have stderr output
        stderr_data = b"".join(e.data for e in stderr_events)
        assert stderr_data == b"error\n"

    def test_simultaneous_stdout_and_stderr(self):
        """Test capturing both stdout and stderr concurrently."""
        events = list(stream_command([
            sys.executable, "-c",
            "import sys; "
            "sys.stdout.buffer.write(b'out'); "
            "sys.stderr.buffer.write(b'err')"
        ]))

        output_events = [e for e in events if isinstance(e, OutputEvent)]

        stdout_data = b"".join(e.data for e in output_events if e.fd == 1)
        stderr_data = b"".join(e.data for e in output_events if e.fd == 2)

        assert stdout_data == b"out"
        assert stderr_data == b"err"

    def test_direct_stdout_and_stderr_descriptors(self, tmp_path):
        """Spawned output can flow directly into caller-provided descriptors."""
        stdout_path = tmp_path / "stdout"
        stderr_path = tmp_path / "stderr"
        stdout_fd = os.open(
            stdout_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        stderr_fd = os.open(
            stderr_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        process = start_command(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            stdout_fd=stdout_fd,
            stderr_fd=stderr_fd,
            capture_stdout=False,
            capture_stderr=False,
        )

        with pytest.raises(OSError) as stdout_error:
            os.fstat(stdout_fd)
        with pytest.raises(OSError) as stderr_error:
            os.fstat(stderr_fd)
        assert stdout_error.value.errno == errno.EBADF
        assert stderr_error.value.errno == errno.EBADF
        assert process.wait() == 0
        assert stdout_path.read_bytes() == b"out\n"
        assert stderr_path.read_bytes() == b"err\n"

    def test_passed_descriptor_is_inherited_and_remains_caller_owned(self):
        """A borrowed descriptor should stay open after child inheritance."""
        read_fd, write_fd = os.pipe()
        try:
            process = start_command(
                [
                    sys.executable,
                    "-c",
                    f"import os; os.write({write_fd}, b'inherited')",
                ],
                pass_fds=(write_fd,),
            )

            assert process.wait() == 0
            os.fstat(write_fd)
            os.close(write_fd)
            write_fd = -1
            assert os.read(read_fd, 64) == b"inherited"
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    def test_passed_descriptors_are_distinct_from_transferred_descriptors(
        self,
        tmp_path,
    ):
        """One descriptor cannot have borrowed and transferred ownership."""
        output_fd = os.open(
            tmp_path / "stdout",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with pytest.raises(
                ValueError,
                match="supplied and passed process file descriptors must differ",
            ):
                start_command(
                    ["true"],
                    stdout_fd=output_fd,
                    pass_fds=(output_fd,),
                    capture_stdout=False,
                )
            os.fstat(output_fd)
        finally:
            os.close(output_fd)

    @pytest.mark.parametrize("failed_allocation", range(1, 6))
    def test_pipe_allocation_failures_close_prior_internal_descriptors(
        self,
        monkeypatch,
        failed_allocation,
    ):
        """A failed pipe allocation should close every prior internal pipe."""
        real_pipe = command_utils.os.pipe
        opened_descriptors = []
        allocation_count = 0

        def injected_pipe():
            nonlocal allocation_count
            allocation_count += 1
            if allocation_count == failed_allocation:
                raise OSError(errno.EMFILE, "injected pipe allocation failure")
            pipe_fds = real_pipe()
            opened_descriptors.extend(pipe_fds)
            return pipe_fds

        monkeypatch.setattr(command_utils.os, "pipe", injected_pipe)

        with pytest.raises(OSError, match="injected pipe allocation failure"):
            start_command(
                ["true"],
                stdin=True,
                extra_fds=[CapturedFd(10), CapturedFd(11)],
            )

        assert allocation_count == failed_allocation
        for file_descriptor in opened_descriptors:
            with pytest.raises(OSError) as descriptor_error:
                os.fstat(file_descriptor)
            assert descriptor_error.value.errno == errno.EBADF

    def test_pipe_allocation_failure_preserves_supplied_descriptors(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A failed spawn setup must not consume caller-supplied descriptors."""
        stdin_fd, stdin_write_fd = os.pipe()
        stdout_fd = os.open(
            tmp_path / "stdout",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        stderr_fd = os.open(
            tmp_path / "stderr",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )

        def fail_pipe():
            raise OSError(errno.EMFILE, "injected pipe allocation failure")

        monkeypatch.setattr(command_utils.os, "pipe", fail_pipe)
        try:
            with pytest.raises(OSError, match="injected pipe allocation failure"):
                start_command(
                    ["true"],
                    stdin_fd=stdin_fd,
                    stdout_fd=stdout_fd,
                    stderr_fd=stderr_fd,
                    extra_fds=[CapturedFd(10)],
                    capture_stdout=False,
                    capture_stderr=False,
                )

            for file_descriptor in (stdin_fd, stdout_fd, stderr_fd):
                os.fstat(file_descriptor)
        finally:
            for file_descriptor in (
                stdin_fd,
                stdin_write_fd,
                stdout_fd,
                stderr_fd,
            ):
                os.close(file_descriptor)

    def test_file_action_failure_closes_internal_descriptors(
        self,
        tmp_path,
        monkeypatch,
    ):
        """A pre-spawn BaseException should retain only supplied descriptors."""
        stdout_fd = os.open(
            tmp_path / "stdout",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        stderr_fd = os.open(
            tmp_path / "stderr",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        real_pipe = command_utils.os.pipe
        real_dup = command_utils.os.dup
        opened_pipe_fds = []
        helper_fds = []
        duplication_count = 0

        def recording_pipe():
            pipe_fds = real_pipe()
            opened_pipe_fds.extend(pipe_fds)
            return pipe_fds

        def fail_second_dup(file_descriptor):
            nonlocal duplication_count
            duplication_count += 1
            if duplication_count == 2:
                raise KeyboardInterrupt("injected file-action failure")
            duplicate_fd = real_dup(file_descriptor)
            helper_fds.append(duplicate_fd)
            return duplicate_fd

        monkeypatch.setattr(command_utils.os, "pipe", recording_pipe)
        monkeypatch.setattr(command_utils.os, "dup", fail_second_dup)
        try:
            with pytest.raises(KeyboardInterrupt, match="file-action failure"):
                start_command(
                    ["true"],
                    stdout_fd=stdout_fd,
                    stderr_fd=stderr_fd,
                    extra_fds=[CapturedFd(stdout_fd), CapturedFd(stderr_fd)],
                    capture_stdout=False,
                    capture_stderr=False,
                )

            assert duplication_count == 2
            for file_descriptor in (*opened_pipe_fds, *helper_fds):
                with pytest.raises(OSError) as descriptor_error:
                    os.fstat(file_descriptor)
                assert descriptor_error.value.errno == errno.EBADF
            os.fstat(stdout_fd)
            os.fstat(stderr_fd)
        finally:
            for file_descriptor in (
                *opened_pipe_fds,
                *helper_fds,
                stdout_fd,
                stderr_fd,
            ):
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass

    def test_post_spawn_construction_failure_closes_and_reaps(
        self,
        tmp_path,
        monkeypatch,
    ):
        """An unreturned process preserves inputs, closes pipes, and is reaped."""
        stdout_fd = os.open(
            tmp_path / "stdout",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        stderr_fd = os.open(
            tmp_path / "stderr",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        real_pipe = command_utils.os.pipe
        real_posix_spawn = command_utils.os.posix_spawn
        opened_pipe_fds = []
        spawned_pids = []

        def recording_pipe():
            pipe_fds = real_pipe()
            opened_pipe_fds.extend(pipe_fds)
            return pipe_fds

        def recording_posix_spawn(*args, **kwargs):
            pid = real_posix_spawn(*args, **kwargs)
            spawned_pids.append(pid)
            return pid

        def fail_process_construction(*args, **kwargs):
            raise MemoryError("injected process construction failure")

        monkeypatch.setattr(command_utils.os, "pipe", recording_pipe)
        monkeypatch.setattr(command_utils.os, "posix_spawn", recording_posix_spawn)
        monkeypatch.setattr(
            command_utils.command_streaming,
            "StreamingProcess",
            fail_process_construction,
        )
        try:
            with pytest.raises(MemoryError, match="process construction failure"):
                start_command(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=True,
                    stdout_fd=stdout_fd,
                    stderr_fd=stderr_fd,
                    capture_stdout=False,
                    capture_stderr=False,
                )

            assert len(spawned_pids) == 1
            for file_descriptor in opened_pipe_fds:
                with pytest.raises(OSError) as descriptor_error:
                    os.fstat(file_descriptor)
                assert descriptor_error.value.errno == errno.EBADF
            for file_descriptor in (stdout_fd, stderr_fd):
                os.fstat(file_descriptor)
            with pytest.raises(ChildProcessError):
                os.waitpid(spawned_pids[0], os.WNOHANG)
        finally:
            for pid in spawned_pids:
                try:
                    waited_pid, _status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    continue
                if waited_pid == 0:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
                    os.waitpid(pid, 0)
            for file_descriptor in (*opened_pipe_fds, stdout_fd, stderr_fd):
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass

    def test_post_spawn_failure_does_not_close_reused_descriptor(
        self,
        monkeypatch,
    ):
        """Exception cleanup must not close a reused descriptor number."""
        real_close = command_utils.os.close
        real_posix_spawn = command_utils.os.posix_spawn
        replacement_fds = []
        spawned_pids = []

        def close_then_reuse(file_descriptor):
            real_close(file_descriptor)
            if not replacement_fds:
                replacement_fd = os.open(os.devnull, os.O_RDONLY)
                assert replacement_fd == file_descriptor
                replacement_fds.append(replacement_fd)

        def recording_posix_spawn(*args, **kwargs):
            pid = real_posix_spawn(*args, **kwargs)
            spawned_pids.append(pid)
            return pid

        def fail_process_construction(*args, **kwargs):
            raise MemoryError("injected process construction failure")

        monkeypatch.setattr(command_utils.os, "close", close_then_reuse)
        monkeypatch.setattr(command_utils.os, "posix_spawn", recording_posix_spawn)
        monkeypatch.setattr(
            command_utils.command_streaming,
            "StreamingProcess",
            fail_process_construction,
        )
        try:
            with pytest.raises(MemoryError, match="process construction failure"):
                start_command(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                )

            assert len(replacement_fds) == 1
            os.fstat(replacement_fds[0])
            assert len(spawned_pids) == 1
            with pytest.raises(ChildProcessError):
                os.waitpid(spawned_pids[0], os.WNOHANG)
        finally:
            for pid in spawned_pids:
                try:
                    waited_pid, _status = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    continue
                if waited_pid == 0:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
                    os.waitpid(pid, 0)
            for file_descriptor in replacement_fds:
                real_close(file_descriptor)


class TestRunCommand:
    """Tests for one-shot command execution."""

    def test_returns_completed_process_with_text_output(self):
        """Test run_command captures text stdout and stderr."""
        result = run_command([
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr)",
        ])

        assert result.returncode == 0
        assert result.stdout == "out\n"
        assert result.stderr == "err\n"

    def test_returns_binary_output(self):
        """Test run_command can capture bytes output."""
        result = run_command(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
            text_output=False,
        )

        assert result.stdout == b"\xff"
        assert result.stderr == b""

    def test_text_output_preserves_undecodable_bytes(self):
        """Git path bytes survive text capture through surrogateescape."""
        result = run_command(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"],
        )

        assert result.stdout.encode(sys.getfilesystemencoding(), "surrogateescape") == b"\xff"

    def test_failed_command_with_check_raises_with_output(self):
        """Test check=True raises CalledProcessError with captured output."""
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            run_command([
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
            ])

        assert exc_info.value.returncode == 3
        assert exc_info.value.stdout == "out\n"
        assert exc_info.value.stderr == "err\n"

    def test_failed_command_without_check_returns_result(self):
        """Test check=False returns a CompletedProcess for nonzero exits."""
        result = run_command(
            [sys.executable, "-c", "import sys; sys.exit(4)"],
            check=False,
        )

        assert result.returncode == 4

    def test_stdin_chunks(self):
        """Test run_command feeds stdin chunks."""
        result = run_command(["cat"], stdin_chunks=[b"hello", b" world"])

        assert result.stdout == "hello world"

    def test_stdin_chunks_without_captured_output(self):
        """Test run_command feeds stdin without captured output fds."""
        result = run_command(
            [
                sys.executable,
                "-c",
                "import sys; assert sys.stdin.buffer.read() == b'hi'",
            ],
            stdin_chunks=[b"hi"],
            capture_stdout=False,
            capture_stderr=False,
        )

        assert result.returncode == 0
        assert result.stdout is None
        assert result.stderr is None


class TestStdinHandling:
    """Tests for stdin streaming."""

    def test_stdin_round_trip(self):
        """Test sending data through stdin and getting it back."""
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[b"hello\n", b"world\n"]
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"hello\nworld\n"

    def test_stdin_closed_event(self):
        """Test that StdinClosedEvent is emitted when stdin is closed."""
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[b"test"]
        ))

        stdin_closed_events = [e for e in events if isinstance(e, StdinClosedEvent)]
        assert len(stdin_closed_events) == 1

    def test_no_stdin_closed_event_when_stdin_not_piped(self):
        """Test that no StdinClosedEvent when stdin is not piped."""
        events = list(stream_command(["printf", "hello"]))

        stdin_closed_events = [e for e in events if isinstance(e, StdinClosedEvent)]
        assert len(stdin_closed_events) == 0

    def test_external_stdin_fd_round_trip(self):
        """Test providing an externally-owned stdin pipe to the child."""
        read_fd, write_fd = os.pipe()
        try:
            proc = start_command(["cat"], stdin_fd=read_fd)
            os.write(write_fd, b"hello\nworld\n")
            os.close(write_fd)
            write_fd = None

            events = list(proc.stream())
        finally:
            if write_fd is not None:
                os.close(write_fd)

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"hello\nworld\n"




    def test_partial_stdin_writes(self):
        """Test handling of partial writes and large data."""
        # Send a large chunk to test partial write handling
        large_data = b"x" * 100000
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[large_data]
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == large_data

    def test_large_stdin_chunks_are_written_in_bounded_slices(self, monkeypatch):
        """Large stdin chunks should not monopolize the event loop."""
        written_lengths = []
        real_write = command_streaming.os.write

        def recording_write(fd, data):
            written_lengths.append(len(data))
            return real_write(fd, data)

        monkeypatch.setattr(command_streaming.os, "write", recording_write)

        large_data = b"x" * (command_streaming._CHUNK_SIZE * 3 + 1)
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[large_data],
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == large_data
        assert max(written_lengths) <= command_streaming._CHUNK_SIZE


class TestExtraFdCapture:
    """Tests for capturing extra child file descriptors."""

    def test_extra_fd_capture(self):
        """Test capturing fd 10 from a child process."""
        # Child writes to fd 10
        events = list(stream_command(
            [sys.executable, "-c", "import os, sys; os.write(10, b'display:1\\n'); os.close(10); sys.exit(0)"],
            extra_fds=[CapturedFd(10)]
        ))

        stderr_data = b"".join(
            e.data for e in events
            if isinstance(e, OutputEvent) and e.fd == 2
        )
        exit_events = [e for e in events if isinstance(e, ExitEvent)]
        fd10_data = b"".join(
            e.data for e in events
            if isinstance(e, OutputEvent) and e.fd == 10
        )

        assert exit_events[0].exit_code == 0, stderr_data.decode(errors="replace")
        assert stderr_data == b""
        assert fd10_data == b"display:1\n"

    def test_multiple_extra_fds(self):
        """Test capturing multiple extra fds."""
        events = list(stream_command(
            [sys.executable, "-c",
             "import os, sys; "
             "os.write(10, b'fd10'); "
             "os.close(10); "
             "os.write(11, b'fd11'); "
             "os.close(11); "
             "sys.exit(0)"],
            extra_fds=[CapturedFd(10), CapturedFd(11)]
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent)]
        exit_events = [e for e in events if isinstance(e, ExitEvent)]

        fd10_data = b"".join(e.data for e in output_events if e.fd == 10)
        fd11_data = b"".join(e.data for e in output_events if e.fd == 11)
        stderr_data = b"".join(e.data for e in output_events if e.fd == 2)

        assert exit_events[0].exit_code == 0, stderr_data.decode(errors="replace")
        assert fd10_data == b"fd10"
        assert fd11_data == b"fd11"

    def test_extra_fd_capture_handles_pipe_fd_collision(self):
        """Test extra fd capture when pipe fds collide with child fd targets."""
        probe_fds = []
        try:
            for _ in range(8):
                probe_fds.append(os.open(os.devnull, os.O_RDONLY))
            first_child_fd = probe_fds[6]
            second_child_fd = probe_fds[7]

            for fd in probe_fds:
                os.close(fd)
            probe_fds = []

            events = list(stream_command(
                [
                    sys.executable,
                    "-c",
                    f"import os, sys; "
                    f"os.write({first_child_fd}, b'fd1'); "
                    f"os.close({first_child_fd}); "
                    f"os.write({second_child_fd}, b'fd2'); "
                    f"os.close({second_child_fd}); "
                    "sys.exit(0)",
                ],
                extra_fds=[
                    CapturedFd(first_child_fd),
                    CapturedFd(second_child_fd),
                ],
            ))
        finally:
            for fd in probe_fds:
                os.close(fd)

        output_events = [e for e in events if isinstance(e, OutputEvent)]
        exit_events = [e for e in events if isinstance(e, ExitEvent)]

        fd1_data = b"".join(e.data for e in output_events if e.fd == first_child_fd)
        fd2_data = b"".join(e.data for e in output_events if e.fd == second_child_fd)
        stderr_data = b"".join(e.data for e in output_events if e.fd == 2)

        assert exit_events[0].exit_code == 0, stderr_data.decode(errors="replace")
        assert fd1_data == b"fd1"
        assert fd2_data == b"fd2"

    def test_duplicate_captured_fd_rejected(self):
        """Test that duplicate CapturedFd entries are rejected."""
        with pytest.raises(ValueError, match="duplicate"):
            start_command(
                ["printf", "hello"],
                extra_fds=[CapturedFd(10), CapturedFd(10)]
            )

    def test_reserved_fd_rejected(self):
        """Test that capturing fd 1 or 2 (or below 3) is rejected."""
        with pytest.raises(ValueError, match="invalid"):
            start_command(
                ["printf", "hello"],
                extra_fds=[CapturedFd(1)]
            )

        with pytest.raises(ValueError, match="invalid"):
            start_command(
                ["printf", "hello"],
                extra_fds=[CapturedFd(2)]
            )

    def test_stdin_and_stdin_fd_are_mutually_exclusive(self):
        """Test that stdin pipe modes cannot be combined."""
        read_fd, write_fd = os.pipe()
        try:
            with pytest.raises(ValueError, match="mutually exclusive"):
                start_command(["cat"], stdin=True, stdin_fd=read_fd)
        finally:
            os.close(read_fd)
            os.close(write_fd)


class TestEventOrdering:
    """Tests for event ordering semantics."""

    def test_exit_event_only_after_output_drained(self):
        """Test that ExitEvent comes after all output is drained."""
        # Use a command that outputs data and exits quickly
        events = list(stream_command(["printf", "hello"]))

        # Find positions of last output and exit event
        last_output_idx = -1
        exit_idx = -1

        for i, event in enumerate(events):
            if isinstance(event, OutputEvent):
                last_output_idx = i
            elif isinstance(event, ExitEvent):
                exit_idx = i

        # Exit must come after all output
        if last_output_idx >= 0:
            assert exit_idx > last_output_idx

    def test_exactly_one_exit_event(self):
        """Test that exactly one ExitEvent is emitted."""
        events = list(stream_command(["printf", "hello"]))

        exit_events = [e for e in events if isinstance(e, ExitEvent)]
        assert len(exit_events) == 1

    def test_exactly_one_stdin_closed_event(self):
        """Test that exactly one StdinClosedEvent when stdin is piped."""
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[b"test"]
        ))

        stdin_closed_events = [e for e in events if isinstance(e, StdinClosedEvent)]
        assert len(stdin_closed_events) == 1

    def test_exit_code_captured(self):
        """Test that non-zero exit codes are captured correctly."""
        events = list(stream_command([sys.executable, "-c", "import sys; sys.exit(42)"]))

        exit_events = [e for e in events if isinstance(e, ExitEvent)]
        assert len(exit_events) == 1
        assert exit_events[0].exit_code == 42


class TestCleanupAndCancellation:
    """Tests for cleanup and early termination."""

    def test_early_iterator_close_cleans_up_child(self):
        """Test that closing iterator early terminates and cleans up child."""
        # Start a long-running process that produces output
        iterator = stream_command([
            sys.executable, "-c",
            "import sys, time; print('starting', flush=True); time.sleep(100)"
        ])

        # Take one event then close
        first_event = next(iterator)
        assert isinstance(first_event, OutputEvent)
        assert first_event.fd == 1
        assert first_event.data == b"starting\n"

        # Explicitly close the generator
        iterator.close()

        # Give a moment for cleanup
        time.sleep(0.1)

        # No exception should have been raised - test passes if we get here

    def test_process_handle_cleanup_on_early_abandonment(self):
        """Test that StreamingProcess cleans up on early iterator close."""
        proc = start_command([
            sys.executable, "-c",
            "import sys, time; print('starting', flush=True); time.sleep(100)"
        ])

        iterator = proc.stream()
        first_event = next(iterator)
        assert isinstance(first_event, OutputEvent)
        assert first_event.fd == 1
        assert first_event.data == b"starting\n"

        iterator.close()

        time.sleep(0.1)

        assert proc._process.poll() is not None


class TestProcessControl:
    """Tests for process control methods."""

    def test_terminate(self):
        """Test terminate() sends SIGTERM."""
        proc = start_command([sys.executable, "-c", "import time; time.sleep(100)"])

        # Terminate the process
        proc.terminate()
        exit_code = proc.wait()

        # Should have been terminated (usually exit code -15 or 143)
        assert exit_code != 0

    def test_kill(self):
        """Test kill() sends SIGKILL."""
        proc = start_command([sys.executable, "-c", "import time; time.sleep(100)"])

        # Kill the process
        proc.kill()
        exit_code = proc.wait()

        # Should have been killed (usually exit code -9 or 137)
        assert exit_code != 0

    def test_wait(self):
        """Test wait() returns exit code."""
        proc = start_command([sys.executable, "-c", "import sys; sys.exit(17)"])

        exit_code = proc.wait()
        assert exit_code == 17

    def test_poll_rejects_unknown_reaped_child_status(self, monkeypatch):
        """A missing child status must not be reported as successful."""
        process = command_streaming.SpawnedProcess(1234)

        def raise_child_process_error(*args):
            raise ChildProcessError("already reaped")

        monkeypatch.setattr(os, "waitpid", raise_child_process_error)

        with pytest.raises(ChildProcessError, match="Cannot determine exit status"):
            process.poll()

        assert process.returncode is None

    def test_wait_rejects_unknown_reaped_child_status(self, monkeypatch):
        """Blocking waits should contextualize a missing child status."""
        process = command_streaming.SpawnedProcess(1234)

        def raise_child_process_error(*args):
            raise ChildProcessError("already reaped")

        monkeypatch.setattr(os, "waitpid", raise_child_process_error)

        with pytest.raises(ChildProcessError, match="Cannot determine exit status"):
            process.wait()

        assert process.returncode is None

    def test_wait_rejects_unsupported_child_status(self, monkeypatch):
        """Blocking waits must not convert an unsupported status to success."""
        process = command_streaming.SpawnedProcess(1234)
        stopped_status = 0x7F
        monkeypatch.setattr(os, "waitpid", lambda *args: (1234, stopped_status))

        with pytest.raises(ChildProcessError, match="unsupported wait status"):
            process.wait()

        assert process.returncode is None

    def test_wait_closes_captured_fds(self):
        """Test wait() closes captured pipe fds when stream() is unused."""
        proc = start_command([
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('out'); sys.stderr.write('err')",
        ])
        captured_fds = list(proc._output_fds)

        exit_code = proc.wait()

        assert exit_code == 0
        assert proc._output_fds == {}
        for fd in captured_fds:
            with pytest.raises(OSError) as exc_info:
                os.fstat(fd)
            assert exc_info.value.errno == errno.EBADF


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_arguments_rejected(self):
        """Test command launch rejects an empty argument list."""
        with pytest.raises(ValueError, match="arguments"):
            start_command([])

    def test_zero_length_chunks(self):
        """Test that zero-length chunks are handled safely."""
        events = list(stream_command(
            ["cat"],
            stdin_chunks=[b"", b"hello", b"", b"world", b""]
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"helloworld"

    def test_command_with_no_output(self):
        """Test command that produces no output."""
        events = list(stream_command(["true"]))

        exit_events = [e for e in events if isinstance(e, ExitEvent)]
        assert len(exit_events) == 1
        assert exit_events[0].exit_code == 0

    def test_no_captured_output_fds_exits_cleanly(self):
        """Test commands can run without capturing stdout, stderr, or extra fds."""
        events = list(stream_command(
            ["printf", "hello"],
            capture_stdout=False,
            capture_stderr=False,
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent)]
        exit_events = [e for e in events if isinstance(e, ExitEvent)]

        assert output_events == []
        assert len(exit_events) == 1
        assert exit_events[0].exit_code == 0

    def test_events_can_only_be_called_once(self):
        """Test that events() can only be called once on a StreamingProcess."""
        proc = start_command(["printf", "hello"])

        # First call should work
        list(proc.stream())

        # Second call should raise error
        with pytest.raises(RuntimeError, match="only be called once"):
            list(proc.stream())


class TestCwdAndEnv:
    """Tests for cwd and env parameters."""

    def test_cwd_parameter(self, tmp_path):
        """Test that cwd parameter works."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        events = list(stream_command(
            ["cat", "test.txt"],
            cwd=str(tmp_path)
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"content"

    def test_cwd_parameter_does_not_use_fork(self, tmp_path, monkeypatch):
        """Test cwd execution stays on the posix_spawn path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        def fail_fork():
            raise AssertionError("fork should not be called")

        monkeypatch.setattr(os, "fork", fail_fork)

        events = list(stream_command(["cat", "test.txt"], cwd=str(tmp_path)))
        output = b"".join(
            event.data
            for event in events
            if isinstance(event, OutputEvent) and event.fd == 1
        )

        assert output == b"content"

    def test_cwd_parameter_preserves_arguments(self, tmp_path):
        """Test cwd shell wrapper does not reinterpret command arguments."""
        events = list(stream_command(
            [
                sys.executable,
                "-c",
                "import os, sys; print(os.getcwd()); print('\\n'.join(sys.argv[1:]))",
                "has space",
                "*.txt",
                "$HOME",
                "semi;colon",
            ],
            cwd=str(tmp_path),
        ))
        output = b"".join(
            event.data
            for event in events
            if isinstance(event, OutputEvent) and event.fd == 1
        )

        assert output.splitlines() == [
            str(tmp_path).encode(),
            b"has space",
            b"*.txt",
            b"$HOME",
            b"semi;colon",
        ]

    def test_cwd_parameter_preserves_leading_dash_directory(self, tmp_path, monkeypatch):
        """Test cwd shell wrapper does not reinterpret leading-dash paths."""
        dashed_dir = tmp_path / "-"
        dashed_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        events = list(stream_command(
            [
                sys.executable,
                "-c",
                "import os; print(os.path.basename(os.getcwd()))",
            ],
            cwd="-",
        ))
        output = b"".join(
            event.data
            for event in events
            if isinstance(event, OutputEvent) and event.fd == 1
        )

        assert output == b"-\n"

    def test_cwd_parameter_with_extra_fd_capture(self, tmp_path):
        """Test extra child fds remain available when cwd is set."""
        events = list(stream_command(
            [
                sys.executable,
                "-c",
                "import os, sys; os.write(10, os.getcwd().encode()); sys.exit(0)",
            ],
            cwd=str(tmp_path),
            extra_fds=[CapturedFd(10)],
        ))

        fd10_data = b"".join(
            e.data for e in events
            if isinstance(e, OutputEvent) and e.fd == 10
        )

        assert fd10_data == str(tmp_path).encode()

    def test_cwd_shell_lookup_ignores_env_path(self, tmp_path):
        """Test cwd shell wrapper lookup does not use the child PATH."""
        marker = tmp_path / "used-sh"
        shell = tmp_path / "sh"
        shell.write_text(
            "#!/bin/sh\n"
            "printf used > \"$SH_MARKER\"\n"
            "exec /bin/sh \"$@\"\n"
        )
        shell.chmod(0o755)

        events = list(stream_command(
            [sys.executable, "-c", "print('ok')"],
            cwd=str(tmp_path),
            env={"PATH": str(tmp_path), "SH_MARKER": str(marker)},
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"ok\n"
        assert not marker.exists()

    def test_cwd_restricted_path_finds_target_command(self, tmp_path):
        """Test cwd execution can find the target in a restricted PATH."""
        command = tmp_path / "hello"
        command.write_text("#!/bin/sh\nprintf cwd-target\n")
        command.chmod(0o755)

        events = list(stream_command(
            ["hello"],
            cwd=str(tmp_path),
            env={"PATH": str(tmp_path)},
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"cwd-target"

    def test_cwd_absolute_command_allows_empty_env_path(self, tmp_path):
        """Test cwd execution of absolute commands does not require PATH."""
        events = list(stream_command(
            [sys.executable, "-c", "print('absolute')"],
            cwd=str(tmp_path),
            env={"PATH": ""},
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"absolute\n"

    def test_command_receives_current_pwd_when_parent_env_is_stale(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Test direct child PWD matches the process cwd."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PWD", "/stale/logical/path")

        result = run_command([
            sys.executable,
            "-c",
            "import os; print(os.environ['PWD'])",
        ])

        assert result.stdout == f"{os.getcwd()}\n"

    def test_env_parameter_pwd_is_copied_and_normalized(self, tmp_path, monkeypatch):
        """Test caller env mappings are not mutated while normalizing PWD."""
        monkeypatch.chdir(tmp_path)
        child_env = {
            "PATH": os.environ.get("PATH", ""),
            "PWD": "/stale/logical/path",
        }

        result = run_command(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ['PWD'])",
            ],
            env=child_env,
        )

        assert result.stdout == f"{os.getcwd()}\n"
        assert child_env["PWD"] == "/stale/logical/path"

    def test_env_parameter(self):
        """Test that env parameter works."""
        events = list(stream_command(
            [sys.executable, "-c", "import os; print(os.environ.get('TEST_VAR', ''))"],
            env={"TEST_VAR": "test_value"}
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert b"test_value" in output_data

    def test_env_path_controls_command_lookup(self, tmp_path):
        """Test executable lookup uses PATH from the child environment."""
        command = tmp_path / "hello-cmd"
        command.write_text("#!/bin/sh\nprintf custom-path\n")
        command.chmod(0o755)

        events = list(stream_command(
            ["hello-cmd"],
            env={"PATH": str(tmp_path)},
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"custom-path"

    def test_env_path_can_make_parent_path_commands_unavailable(self):
        """Test command lookup does not fall back to the parent PATH."""
        with pytest.raises(FileNotFoundError):
            start_command(["true"], env={"PATH": ""})

    def test_empty_env_path_searches_current_directory(self, tmp_path, monkeypatch):
        """Test an empty PATH entry searches the current directory."""
        command = tmp_path / "hello-current"
        command.write_text("#!/bin/sh\nprintf current-dir\n")
        command.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        events = list(stream_command(
            ["hello-current"],
            env={"PATH": ""},
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert output_data == b"current-dir"
