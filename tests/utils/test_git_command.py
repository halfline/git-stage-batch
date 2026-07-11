"""Tests for git command execution utilities."""

from git_stage_batch.utils import git_command as git_command_utils
from git_stage_batch.utils import git_index_lock
from git_stage_batch.utils.git_command import stream_git_command
from git_stage_batch.utils.git_command import stream_git_diff

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_index_lock import wait_for_git_index_lock


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, cwd=repo, capture_output=True)

    return repo


class TestRunGitCommand:
    """Tests for run_git_command function."""

    def test_successful_command_returns_result(self, temp_git_repo):
        """Test that successful git command returns CompletedProcess."""
        result = run_git_command(["status", "--short"])

        assert result.returncode == 0
        assert isinstance(result.stdout, str)

    def test_failed_command_with_check_raises(self, temp_git_repo):
        """Test that failed command with check=True raises CalledProcessError."""
        with pytest.raises(subprocess.CalledProcessError):
            run_git_command(["invalid-command"])

    def test_failed_command_without_check_returns_result(self, temp_git_repo):
        """Test that failed command with check=False returns result."""
        result = run_git_command(["invalid-command"], check=False)

        assert result.returncode != 0

    def test_text_output_returns_strings(self, temp_git_repo):
        """Test that text_output=True returns string output."""
        result = run_git_command(["status"], text_output=True)

        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)

    def test_binary_output_returns_bytes(self, temp_git_repo):
        """Test that text_output=False returns bytes output."""
        result = run_git_command(["show", "HEAD:README.md"], text_output=False)

        assert result.stdout == b"# Test\n"
        assert isinstance(result.stderr, bytes)

    def test_captures_stdout(self, temp_git_repo):
        """Test that stdout is captured."""
        result = run_git_command(["rev-parse", "--git-dir"])

        assert ".git" in result.stdout

    def test_waits_for_index_lock_by_default(self, monkeypatch):
        """Index-writing commands should wait for a pre-existing index lock."""
        calls = []
        command_env = {"CUSTOM": "1"}

        def fake_wait(*, cwd, env, **_kwargs):
            calls.append(("wait", cwd, env))

        def fake_run_command(arguments, stdin_chunks=None, **kwargs):
            calls.append(("run", arguments, stdin_chunks, kwargs))
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        monkeypatch.setattr(git_index_lock, "wait_for_git_index_lock", fake_wait)
        monkeypatch.setattr(git_command_utils, "run_command", fake_run_command)

        run_git_command(["add", "--", "file.txt"], env=command_env, cwd="/repo")

        assert calls[0] == ("wait", "/repo", command_env)
        assert calls[1][0] == "run"
        assert calls[1][3]["env"] is command_env

    def test_retries_transient_index_lock_error(self, monkeypatch):
        """Index-writing commands should retry when Git loses the lock race."""
        calls = []

        def fake_wait(*, cwd, env, timeout_seconds, **_kwargs):
            calls.append(("wait", cwd, env, timeout_seconds))

        def fake_run_command(arguments, stdin_chunks=None, **kwargs):
            calls.append(("run", arguments, stdin_chunks, kwargs))
            if len([call for call in calls if call[0] == "run"]) == 1:
                return subprocess.CompletedProcess(
                    arguments,
                    128,
                    stdout="",
                    stderr="fatal: Unable to create '/repo/.git/index.lock': File exists.\n",
                )
            return subprocess.CompletedProcess(arguments, 0, stdout="ok\n", stderr="")

        monkeypatch.setattr(git_index_lock, "wait_for_git_index_lock", fake_wait)
        monkeypatch.setattr(git_command_utils, "run_command", fake_run_command)

        result = run_git_command(["apply", "--cached"], check=False, cwd="/repo")

        assert result.returncode == 0
        assert result.stdout == "ok\n"
        assert [call[0] for call in calls] == ["wait", "run", "wait", "run"]

    def test_retries_index_lock_error_with_reusable_stdin(self, monkeypatch):
        """Retried index-writing commands must resend stdin chunks."""
        seen_stdin = []

        def fake_wait(**_kwargs):
            pass

        def fake_run_command(arguments, stdin_chunks=None, **_kwargs):
            seen_stdin.append(list(stdin_chunks or []))
            if len(seen_stdin) == 1:
                return subprocess.CompletedProcess(
                    arguments,
                    128,
                    stdout="",
                    stderr="fatal: Unable to create '/repo/.git/index.lock': File exists.\n",
                )
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        def patch_chunks():
            yield b"diff --git a/file.txt b/file.txt\n"
            yield b"+new line\n"

        monkeypatch.setattr(git_index_lock, "wait_for_git_index_lock", fake_wait)
        monkeypatch.setattr(git_command_utils, "run_command", fake_run_command)

        result = run_git_command(
            ["apply", "--cached"],
            stdin_chunks=patch_chunks(),
            check=False,
        )

        assert result.returncode == 0
        assert seen_stdin == [
            [b"diff --git a/file.txt b/file.txt\n", b"+new line\n"],
            [b"diff --git a/file.txt b/file.txt\n", b"+new line\n"],
        ]

    def test_disables_optional_locks_for_read_only_commands(self, monkeypatch):
        """Read-only commands should opt out of Git's optional index refresh locks."""
        captured = {}

        def fail_wait(**_kwargs):
            raise AssertionError("read-only command should not wait for the index lock")

        def fake_run_command(arguments, stdin_chunks=None, **kwargs):
            captured["arguments"] = arguments
            captured["stdin_chunks"] = stdin_chunks
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        monkeypatch.setattr(git_index_lock, "wait_for_git_index_lock", fail_wait)
        monkeypatch.setattr(git_command_utils, "run_command", fake_run_command)

        original_env = {"CUSTOM": "1"}
        run_git_command(["status", "--short"], env=original_env, requires_index_lock=False)

        assert captured["arguments"] == ["git", "status", "--short"]
        assert captured["kwargs"]["env"]["CUSTOM"] == "1"
        assert captured["kwargs"]["env"]["GIT_OPTIONAL_LOCKS"] == "0"
        assert original_env == {"CUSTOM": "1"}


class TestWaitForGitIndexLock:
    """Tests for Git index lock waiting."""

    def test_returns_when_lock_absent(self, tmp_path, monkeypatch):
        """Absent index locks should not delay command startup."""
        sleep_calls = []

        monkeypatch.setattr(git_index_lock, "_git_index_lock_path", lambda **_kwargs: tmp_path / "index.lock")
        monkeypatch.setattr(git_index_lock.time, "sleep", lambda duration: sleep_calls.append(duration))

        wait_for_git_index_lock()

        assert sleep_calls == []

    def test_polls_until_lock_disappears(self, tmp_path, monkeypatch):
        """A transient index lock should delay a locking command until it disappears."""
        index_lock = tmp_path / "index.lock"
        index_lock.write_text("")
        sleep_calls = []

        def fake_sleep(duration):
            sleep_calls.append(duration)
            index_lock.unlink()

        monkeypatch.setattr(git_index_lock, "_git_index_lock_path", lambda **_kwargs: index_lock)
        monkeypatch.setattr(git_index_lock.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(git_index_lock.time, "sleep", fake_sleep)

        wait_for_git_index_lock(timeout_seconds=1.0, poll_seconds=0.05)

        assert sleep_calls == [0.05]

    def test_stops_after_timeout(self, tmp_path, monkeypatch):
        """Persistent index locks should only block for the configured timeout."""
        index_lock = tmp_path / "index.lock"
        index_lock.write_text("")
        times = iter([0.0, 0.0, 0.2])
        sleep_calls = []

        monkeypatch.setattr(git_index_lock, "_git_index_lock_path", lambda **_kwargs: index_lock)
        monkeypatch.setattr(git_index_lock.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(git_index_lock.time, "sleep", lambda duration: sleep_calls.append(duration))

        wait_for_git_index_lock(timeout_seconds=0.1, poll_seconds=0.05)

        assert index_lock.exists()
        assert sleep_calls == [0.05]

    def test_ignores_non_repository(self, monkeypatch):
        """Lock waiting should defer non-repository errors to the git command."""
        sleep_calls = []

        def fail_git_directory(**_kwargs):
            raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

        monkeypatch.setattr(git_index_lock, "_git_index_lock_path", fail_git_directory)
        monkeypatch.setattr(git_index_lock.time, "sleep", lambda duration: sleep_calls.append(duration))

        wait_for_git_index_lock()

        assert sleep_calls == []

    def test_uses_custom_index_file_lock_path(self, tmp_path, monkeypatch):
        """Temporary index commands should wait on their own index lock."""
        index_file = tmp_path / "temporary.index"
        index_lock = Path(f"{index_file}.lock")
        index_lock.write_text("")
        sleep_calls = []

        def fail_git_directory(*_args, **_kwargs):
            raise AssertionError("custom index lock path should not inspect the repository git dir")

        def fake_sleep(duration):
            sleep_calls.append(duration)
            index_lock.unlink()

        monkeypatch.setattr(git_index_lock, "run_command", fail_git_directory)
        monkeypatch.setattr(git_index_lock.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(git_index_lock.time, "sleep", fake_sleep)

        wait_for_git_index_lock(
            env={"GIT_INDEX_FILE": str(index_file)},
            timeout_seconds=1.0,
            poll_seconds=0.05,
        )

        assert sleep_calls == [0.05]


class TestStreamGitCommand:
    """Tests for stream_git_command function."""

    def test_stream_git_command_success(self, temp_git_repo):
        """Test streaming a successful git command."""

        # Create a file with multiple lines
        test_file = temp_git_repo / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3\n")
        subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Stream the diff
        lines = list(stream_git_command(["diff", "--cached"]))
        assert len(lines) > 0
        # Should have lines from the diff
        assert any(b"line 1" in line for line in lines)

    def test_stream_git_command_early_termination(self, temp_git_repo):
        """Test that stream can be terminated early without error."""

        # Create a large file
        large_content = "\n".join([f"line {i}" for i in range(1000)])
        test_file = temp_git_repo / "large.txt"
        test_file.write_text(large_content)
        subprocess.run(["git", "add", "large.txt"], check=True, cwd=temp_git_repo, capture_output=True)

        # Take only first few lines
        stream = stream_git_command(["diff", "--cached"])
        first_lines = []
        for i, line in enumerate(stream):
            first_lines.append(line)
            if i >= 5:
                break

        assert len(first_lines) == 6

    def test_stream_git_command_failure(self, temp_git_repo):
        """Test that streaming a failing command raises error."""

        with pytest.raises(subprocess.CalledProcessError):
            # Consume the entire stream to trigger error check
            list(stream_git_command(["invalid-command"]))


class TestStreamGitDiff:
    """Tests for stream_git_diff function."""

    def test_stream_git_diff_reads_cached_diff(self, temp_git_repo):
        """Test streaming a cached diff through keyword arguments."""
        test_file = temp_git_repo / "cached.txt"
        test_file.write_text("cached line\n")
        subprocess.run(
            ["git", "add", "cached.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        lines = list(stream_git_diff(cached=True, context_lines=0))

        assert any(line == b"+++ b/cached.txt\n" for line in lines)
        assert any(line == b"+cached line\n" for line in lines)

    def test_stream_git_diff_accepts_base_target_and_paths(self, temp_git_repo):
        """Test streaming a path-filtered diff between two commits."""
        readme = temp_git_repo / "README.md"
        other_file = temp_git_repo / "other.txt"
        readme.write_text("# Changed\n")
        other_file.write_text("other\n")
        subprocess.run(
            ["git", "add", "README.md", "other.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Update files"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        lines = list(
            stream_git_diff(
                base="HEAD~1",
                target="HEAD",
                context_lines=0,
                paths=["README.md"],
            )
        )

        assert any(line == b"diff --git a/README.md b/README.md\n" for line in lines)
        assert any(line == b"+# Changed\n" for line in lines)
        assert not any(b"other.txt" in line for line in lines)

    def test_stream_git_diff_disables_color_by_default(self, temp_git_repo):
        """Test default diff output remains uncolored."""
        run_git_command(["config", "color.ui", "always"])
        test_file = temp_git_repo / "color.txt"
        test_file.write_text("color line\n")
        subprocess.run(
            ["git", "add", "color.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        lines = list(stream_git_diff(cached=True))

        assert lines
        assert not any(b"\x1b[" in line for line in lines)

    def test_stream_git_diff_overrides_format_changing_configuration(
        self,
        temp_git_repo,
    ):
        """Repository configuration cannot alter consumed diff structure."""
        subprocess.run(
            ["git", "config", "diff.noprefix", "true"],
            check=True,
            cwd=temp_git_repo,
        )
        subprocess.run(
            ["git", "config", "diff.external", "false"],
            check=True,
            cwd=temp_git_repo,
        )
        test_file = temp_git_repo / "configured.txt"
        test_file.write_text("configured\n")
        subprocess.run(
            ["git", "add", test_file.name],
            check=True,
            cwd=temp_git_repo,
        )

        lines = list(stream_git_diff(cached=True))

        assert b"diff --git a/configured.txt b/configured.txt\n" in lines
