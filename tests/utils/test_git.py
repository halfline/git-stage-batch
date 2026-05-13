"""Tests for git command execution utilities."""

from git_stage_batch.utils import git as git_utils
from git_stage_batch.utils.git import stream_git_command
from git_stage_batch.utils.git import stream_git_diff
from git_stage_batch.utils.git import resolve_file_path_to_repo_relative
from git_stage_batch.utils.git import read_gitignore_lines
from git_stage_batch.utils.git import get_gitignore_path
from git_stage_batch.utils.git import write_gitignore_lines
from git_stage_batch.utils.git import add_file_to_gitignore
from git_stage_batch.utils.git import remove_file_from_gitignore

import subprocess
from pathlib import Path

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.git import (
    create_git_blob,
    get_git_repository_root_path,
    git_commit_tree,
    git_read_tree,
    git_update_index,
    git_write_tree,
    require_git_repository,
    run_git_command,
    temp_git_index,
    wait_for_git_index_lock,
)


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

        def fake_wait(*, cwd, env):
            calls.append(("wait", cwd, env))

        def fake_run_command(arguments, stdin_chunks=None, **kwargs):
            calls.append(("run", arguments, stdin_chunks, kwargs))
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        monkeypatch.setattr(git_utils, "wait_for_git_index_lock", fake_wait)
        monkeypatch.setattr(git_utils, "run_command", fake_run_command)

        run_git_command(["add", "--", "file.txt"], env=command_env, cwd="/repo")

        assert calls[0] == ("wait", "/repo", command_env)
        assert calls[1][0] == "run"
        assert calls[1][3]["env"] is command_env

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

        monkeypatch.setattr(git_utils, "wait_for_git_index_lock", fail_wait)
        monkeypatch.setattr(git_utils, "run_command", fake_run_command)

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

        monkeypatch.setattr(git_utils, "_git_index_lock_path", lambda **_kwargs: tmp_path / "index.lock")
        monkeypatch.setattr(git_utils.time, "sleep", lambda duration: sleep_calls.append(duration))

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

        monkeypatch.setattr(git_utils, "_git_index_lock_path", lambda **_kwargs: index_lock)
        monkeypatch.setattr(git_utils.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(git_utils.time, "sleep", fake_sleep)

        wait_for_git_index_lock(timeout_seconds=1.0, poll_seconds=0.05)

        assert sleep_calls == [0.05]

    def test_stops_after_timeout(self, tmp_path, monkeypatch):
        """Persistent index locks should only block for the configured timeout."""
        index_lock = tmp_path / "index.lock"
        index_lock.write_text("")
        times = iter([0.0, 0.0, 0.2])
        sleep_calls = []

        monkeypatch.setattr(git_utils, "_git_index_lock_path", lambda **_kwargs: index_lock)
        monkeypatch.setattr(git_utils.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(git_utils.time, "sleep", lambda duration: sleep_calls.append(duration))

        wait_for_git_index_lock(timeout_seconds=0.1, poll_seconds=0.05)

        assert index_lock.exists()
        assert sleep_calls == [0.05]

    def test_ignores_non_repository(self, monkeypatch):
        """Lock waiting should defer non-repository errors to the git command."""
        sleep_calls = []

        def fail_git_directory(**_kwargs):
            raise subprocess.CalledProcessError(128, ["git", "rev-parse"])

        monkeypatch.setattr(git_utils, "_git_index_lock_path", fail_git_directory)
        monkeypatch.setattr(git_utils.time, "sleep", lambda duration: sleep_calls.append(duration))

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

        monkeypatch.setattr(git_utils, "run_command", fail_git_directory)
        monkeypatch.setattr(git_utils.time, "monotonic", lambda: 0.0)
        monkeypatch.setattr(git_utils.time, "sleep", fake_sleep)

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


class TestGitIndexPlumbing:
    """Tests for temporary index plumbing helpers."""

    def test_temp_index_builds_commit_without_touching_main_index(self, temp_git_repo):
        """Test creating a commit from a temporary index."""
        blob_sha = create_git_blob([b"from temp index\n"])

        with temp_git_index() as env:
            temp_index_path = Path(env["GIT_INDEX_FILE"])
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path="nested/file.txt",
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        assert not temp_index_path.exists()

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Temporary index commit",
        )
        result = run_git_command(["show", f"{commit_sha}:nested/file.txt"])

        assert result.stdout == "from temp index\n"
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_update_index_cacheinfo_handles_comma_paths(self, temp_git_repo):
        """Test that cacheinfo paths are passed as separate arguments."""
        blob_sha = create_git_blob([b"comma path\n"])
        file_path = "dir/name,with,commas.txt"

        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(
                mode="100644",
                blob_sha=blob_sha,
                file_path=file_path,
                env=env,
            )
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Comma path commit",
        )
        result = run_git_command(["show", f"{commit_sha}:{file_path}"])

        assert result.stdout == "comma path\n"

    def test_update_index_force_remove_deletes_index_entry(self, temp_git_repo):
        """Test force-removing a path from a temporary index."""
        with temp_git_index() as env:
            git_read_tree("HEAD", env=env)
            git_update_index(file_path="README.md", force_remove=True, env=env)
            tree_sha = git_write_tree(env=env)

        commit_sha = git_commit_tree(
            tree_sha,
            parents=["HEAD"],
            message="Remove file from temp index",
        )
        result = run_git_command(["show", f"{commit_sha}:README.md"], check=False)

        assert result.returncode != 0
        assert run_git_command(["status", "--short"]).stdout == ""

    def test_update_index_rejects_ambiguous_modes(self, temp_git_repo):
        """Test that update-index helper modes are explicit."""
        with pytest.raises(ValueError, match="mode and blob_sha are required"):
            git_update_index(file_path="README.md")

        with pytest.raises(ValueError, match="cannot be used with force_remove"):
            git_update_index(
                file_path="README.md",
                mode="100644",
                blob_sha="0" * 40,
                force_remove=True,
            )


class TestRequireGitRepository:
    """Tests for require_git_repository function."""

    def test_succeeds_in_git_repository(self, temp_git_repo):
        """Test that function succeeds when inside a git repository."""
        # Should not raise
        require_git_repository()

    def test_exits_outside_git_repository(self, tmp_path, monkeypatch):
        """Test that function exits with error outside git repository."""
        # Change to non-git directory
        monkeypatch.chdir(tmp_path)

        with pytest.raises(CommandError):
            require_git_repository()


class TestGetGitRepositoryRootPath:
    """Tests for get_git_repository_root_path function."""

    def test_returns_repository_root(self, temp_git_repo):
        """Test that function returns the repository root path."""
        root = get_git_repository_root_path()

        assert isinstance(root, Path)
        assert root.is_absolute()
        assert (root / ".git").exists()

    def test_returns_same_path_from_subdirectory(self, temp_git_repo, monkeypatch):
        """Test that function returns root even from subdirectory."""
        # Create subdirectory and change to it
        subdir = temp_git_repo / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        root = get_git_repository_root_path()

        assert root == temp_git_repo


class TestResolveFilePathToRepoRelative:
    """Tests for resolve_file_path_to_repo_relative function."""

    def test_resolve_file_path_to_repo_relative_relative(self, temp_git_repo):
        """Test that relative paths are returned as-is."""

        result = resolve_file_path_to_repo_relative("src/file.py")
        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_absolute(self, temp_git_repo):
        """Test that absolute paths inside repo are made relative."""

        absolute_path = str(temp_git_repo / "src" / "file.py")
        result = resolve_file_path_to_repo_relative(absolute_path)
        assert result == "src/file.py"

    def test_resolve_file_path_to_repo_relative_outside_repo(self, temp_git_repo, tmp_path):
        """Test that paths outside repo are returned as-is."""

        outside_path = str(tmp_path / "outside.txt")
        result = resolve_file_path_to_repo_relative(outside_path)
        assert result == outside_path


class TestGitignoreManipulation:
    """Tests for .gitignore manipulation functions."""

    def test_read_gitignore_lines_nonexistent(self, temp_git_repo):
        """Test reading .gitignore when it doesn't exist."""

        lines = read_gitignore_lines()
        assert lines == []

    def test_read_gitignore_lines_existing(self, temp_git_repo):
        """Test reading existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n.env\n")

        lines = read_gitignore_lines()
        assert lines == ["*.pyc\n", "__pycache__/\n", ".env\n"]

    def test_write_gitignore_lines(self, temp_git_repo):
        """Test writing .gitignore lines."""

        lines = ["*.pyc\n", "__pycache__/\n", ".env\n"]
        write_gitignore_lines(lines)

        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert content == "*.pyc\n__pycache__/\n.env\n"

    def test_add_file_to_gitignore_new(self, temp_git_repo):
        """Test adding a file to .gitignore when .gitignore doesn't exist."""

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_existing(self, temp_git_repo):
        """Test adding a file to existing .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n__pycache__/\n")

        add_file_to_gitignore("test.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "__pycache__/\n" in lines
        assert "test.txt\n" in lines

    def test_add_file_to_gitignore_no_duplicates(self, temp_git_repo):
        """Test adding a file twice doesn't create duplicates."""

        add_file_to_gitignore("test.txt")
        add_file_to_gitignore("test.txt")

        content = get_gitignore_path().read_text()
        # Should only appear once
        assert content.count("test.txt") == 1

    def test_add_file_to_gitignore_preserves_no_trailing_newline(self, temp_git_repo):
        """Test adding to .gitignore when existing file has no trailing newline."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc")  # No trailing newline

        add_file_to_gitignore("test.txt")

        content = gitignore.read_text()
        assert content == "*.pyc\ntest.txt\n"

    def test_remove_file_from_gitignore_with_marker(self, temp_git_repo):
        """Test removing a file from .gitignore."""

        add_file_to_gitignore("test.txt")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines

    def test_remove_file_from_gitignore_without_marker(self, temp_git_repo):
        """Test that we can remove any entry from .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("test.txt\n*.pyc\n")

        removed = remove_file_from_gitignore("test.txt")
        assert removed is True

        # Entry should be removed
        lines = read_gitignore_lines()
        assert "test.txt\n" not in lines
        # Other entries should remain
        assert "*.pyc\n" in lines

    def test_remove_file_from_gitignore_preserves_other_entries(self, temp_git_repo):
        """Test that removing one entry preserves others."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        add_file_to_gitignore("test1.txt")
        add_file_to_gitignore("test2.txt")

        remove_file_from_gitignore("test1.txt")

        lines = read_gitignore_lines()
        assert "*.pyc\n" in lines
        assert "test1.txt\n" not in lines
        assert "test2.txt\n" in lines

    def test_remove_file_from_gitignore_nonexistent(self, temp_git_repo):
        """Test removing a file that doesn't exist in .gitignore."""

        gitignore = get_gitignore_path()
        gitignore.write_text("*.pyc\n")

        removed = remove_file_from_gitignore("nonexistent.txt")
        assert removed is False

        # Original content unchanged
        assert gitignore.read_text() == "*.pyc\n"
