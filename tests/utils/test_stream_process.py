"""Tests for POSIX subprocess streaming."""

import os
import time

import sys

import pytest

from git_stage_batch.utils.command import (
    CapturedFd,
    ExitEvent,
    OutputEvent,
    StdinClosedEvent,
    start_command,
    stream_command,
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


class TestEdgeCases:
    """Tests for edge cases."""

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

    def test_env_parameter(self):
        """Test that env parameter works."""
        events = list(stream_command(
            [sys.executable, "-c", "import os; print(os.environ.get('TEST_VAR', ''))"],
            env={"TEST_VAR": "test_value"}
        ))

        output_events = [e for e in events if isinstance(e, OutputEvent) and e.fd == 1]
        output_data = b"".join(e.data for e in output_events)

        assert b"test_value" in output_data
