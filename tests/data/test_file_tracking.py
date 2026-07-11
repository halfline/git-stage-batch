"""Tests for auto_add_untracked_files function."""

import subprocess

import pytest

from git_stage_batch.data.file_tracking import auto_add_untracked_files
from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.paths import (
    ensure_state_directory_exists,
    get_auto_added_files_file_path,
)

EMPTY_BLOB_HASH = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


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


class TestAutoAddUntrackedFiles:
    """Tests for auto_add_untracked_files."""

    def test_auto_add_single_untracked_file(self, temp_git_repo):
        """Test auto-adding a single untracked file."""
        ensure_state_directory_exists()

        # Create an untracked file
        new_file = temp_git_repo / "new.txt"
        new_file.write_text("content\n")

        auto_add_untracked_files()

        # Check file is in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "new.txt" in auto_added

        # Verify file was added with -N (intent-to-add)
        result = subprocess.run(
            ["git", "ls-files", "--", "new.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" in result.stdout

        # Verify file content is not staged by git add -N.
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "new.txt" not in result.stdout

    def test_auto_add_multiple_untracked_files(self, temp_git_repo):
        """Test auto-adding multiple untracked files."""
        ensure_state_directory_exists()

        # Create multiple untracked files
        (temp_git_repo / "file1.txt").write_text("content1\n")
        (temp_git_repo / "file2.py").write_text("print('hello')\n")
        (temp_git_repo / "file3.md").write_text("# Header\n")

        auto_add_untracked_files()

        # Check all files are in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "file1.txt" in auto_added
        assert "file2.py" in auto_added
        assert "file3.md" in auto_added

    def test_auto_add_respects_gitignore(self, temp_git_repo):
        """Test that files matching .gitignore patterns are not auto-added."""
        ensure_state_directory_exists()

        # Create .gitignore
        gitignore = temp_git_repo / ".gitignore"
        gitignore.write_text("*.log\n*.tmp\n")
        subprocess.run(["git", "add", ".gitignore"], check=True, cwd=temp_git_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add gitignore"], check=True, cwd=temp_git_repo, capture_output=True)

        # Create files, some matching .gitignore
        (temp_git_repo / "normal.txt").write_text("content\n")
        (temp_git_repo / "debug.log").write_text("log content\n")
        (temp_git_repo / "temp.tmp").write_text("temp content\n")

        auto_add_untracked_files()

        # Check only normal file was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "normal.txt" in auto_added
        assert "debug.log" not in auto_added
        assert "temp.tmp" not in auto_added

    def test_auto_add_is_idempotent(self, temp_git_repo):
        """Test that calling auto_add multiple times doesn't cause issues."""
        ensure_state_directory_exists()

        # Create an untracked file
        (temp_git_repo / "file.txt").write_text("content\n")

        # Call auto_add twice
        auto_add_untracked_files()
        auto_add_untracked_files()

        # File should only appear once in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added.count("file.txt") == 1

    def test_auto_add_accepts_target_paths(self, temp_git_repo):
        """Test auto-adding only the requested untracked paths."""
        ensure_state_directory_exists()

        (temp_git_repo / "target.txt").write_text("target\n")
        (temp_git_repo / "other.txt").write_text("other\n")

        auto_add_untracked_files(["target.txt"])

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added == ["target.txt"]

        result = subprocess.run(
            ["git", "ls-files", "--", "target.txt", "other.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "target.txt" in result.stdout
        assert "other.txt" not in result.stdout

    def test_auto_add_reruns_for_recorded_untracked_path(self, temp_git_repo):
        """Test re-adding a recorded path after its intent-to-add entry is removed."""
        ensure_state_directory_exists()

        (temp_git_repo / "file.txt").write_text("content\n")
        auto_add_untracked_files()

        subprocess.run(
            ["git", "restore", "--staged", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
        )

        auto_add_untracked_files(["file.txt"])

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added.count("file.txt") == 1

        result = subprocess.run(
            ["git", "ls-files", "--", "file.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "file.txt"

    def test_auto_add_handles_no_untracked_files(self, temp_git_repo):
        """Test auto_add when there are no untracked files."""
        ensure_state_directory_exists()

        # No untracked files exist
        auto_add_untracked_files()

        # Auto-added list should be empty
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert auto_added == []

    def test_auto_add_with_nested_directories(self, temp_git_repo):
        """Test auto-adding files in nested directories."""
        ensure_state_directory_exists()

        # Create nested directory structure
        subdir = temp_git_repo / "src" / "lib"
        subdir.mkdir(parents=True)
        (subdir / "module.py").write_text("def foo(): pass\n")

        auto_add_untracked_files()

        # Check nested file was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "src/lib/module.py" in auto_added

        # Verify it's tracked
        result = subprocess.run(
            ["git", "ls-files", "--", "src/lib/module.py"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert "src/lib/module.py" in result.stdout

    def test_auto_add_after_some_already_added(self, temp_git_repo):
        """Test auto_add when some files are already auto-added."""
        ensure_state_directory_exists()

        # Create and auto-add first file
        (temp_git_repo / "file1.txt").write_text("content1\n")
        auto_add_untracked_files()

        # Create second file
        (temp_git_repo / "file2.txt").write_text("content2\n")
        auto_add_untracked_files()

        # Both should be in auto-added list
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "file1.txt" in auto_added
        assert "file2.txt" in auto_added

    def test_auto_add_with_spaces_in_filename(self, temp_git_repo):
        """Test auto-adding files with spaces in the name."""
        ensure_state_directory_exists()

        # Create file with spaces
        file_with_spaces = temp_git_repo / "my file.txt"
        file_with_spaces.write_text("content\n")

        auto_add_untracked_files()

        # Check it was auto-added
        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "my file.txt" in auto_added

    def test_auto_add_preserves_file_kinds_and_unicode_paths(self, temp_git_repo):
        """Bulk startup should preserve empty, binary, symlink, and Unicode paths."""
        ensure_state_directory_exists()
        (temp_git_repo / "empty.txt").write_bytes(b"")
        (temp_git_repo / "binary.bin").write_bytes(b"\x00\xffbinary")
        (temp_git_repo / "unicodé.txt").write_text("accented\n")
        (temp_git_repo / "target.txt").write_text("target\n")
        (temp_git_repo / "link").symlink_to("target.txt")

        auto_add_untracked_files()

        assert set(read_file_paths_file(get_auto_added_files_file_path())) == {
            "binary.bin",
            "empty.txt",
            "link",
            "target.txt",
            "unicodé.txt",
        }

    def test_auto_add_marks_untracked_embedded_git_repository_as_gitlink(self, temp_git_repo):
        """Test that untracked embedded repositories are auto-added as gitlinks."""
        ensure_state_directory_exists()

        embedded_repo = temp_git_repo / "embedded"
        embedded_repo.mkdir()
        subprocess.run(["git", "init"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=embedded_repo, capture_output=True)
        (embedded_repo / "file.txt").write_text("content\n")
        subprocess.run(["git", "add", "file.txt"], check=True, cwd=embedded_repo, capture_output=True)
        subprocess.run(["git", "commit", "-m", "Add file"], check=True, cwd=embedded_repo, capture_output=True)
        embedded_oid = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            cwd=embedded_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()

        auto_add_untracked_files()

        auto_added = read_file_paths_file(get_auto_added_files_file_path())
        assert "embedded" in auto_added
        assert "embedded/" not in auto_added

        result = subprocess.run(
            ["git", "ls-files", "--stage", "--", "embedded"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == f"160000 {EMPTY_BLOB_HASH} 0\tembedded"

        diff_result = subprocess.run(
            ["git", "diff", "--ignore-submodules=none", "--submodule=short", "HEAD", "--", "embedded"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert f"+Subproject commit {embedded_oid}" in diff_result.stdout

    def test_auto_add_process_and_state_writes_are_constant(self, temp_git_repo, monkeypatch):
        """A large candidate set should use one add and one manifest write."""
        from git_stage_batch.data import file_tracking

        candidates = [f"generated/file-{index}.txt" for index in range(10_000)]
        add_calls = []
        write_calls = []

        monkeypatch.setattr(
            file_tracking,
            "list_untracked_files",
            lambda paths=None: candidates,
        )
        monkeypatch.setattr(
            file_tracking,
            "_embedded_git_repository_index_path",
            lambda path: None,
        )

        def fake_add(paths, *, intent_to_add=False, check=True):
            add_calls.append((tuple(paths), intent_to_add, check))
            return subprocess.CompletedProcess(["git", "add"], 0, "", "")

        def fake_write(path, paths):
            write_calls.append((path, tuple(paths)))

        monkeypatch.setattr(file_tracking, "git_add_paths_from_stdin", fake_add)
        monkeypatch.setattr(file_tracking, "write_file_paths_file", fake_write)

        auto_add_untracked_files()

        assert len(add_calls) == 1
        assert len(add_calls[0][0]) == 10_000
        assert len(write_calls) == 1
        assert len(write_calls[0][1]) == 10_000

    def test_auto_add_reports_large_bulk_transition(
        self,
        temp_git_repo,
        monkeypatch,
        capsys,
    ):
        """Interactive startup should expose progress for a large candidate set."""
        from git_stage_batch.data import file_tracking

        candidates = [f"file-{index}.txt" for index in range(1_000)]
        monkeypatch.setattr(
            file_tracking,
            "list_untracked_files",
            lambda paths=None: candidates,
        )
        monkeypatch.setattr(
            file_tracking,
            "_embedded_git_repository_index_path",
            lambda path: None,
        )
        monkeypatch.setattr(
            file_tracking,
            "git_add_paths_from_stdin",
            lambda paths, **kwargs: subprocess.CompletedProcess(
                ["git", "add"], 0, "", ""
            ),
        )
        monkeypatch.setattr(file_tracking, "write_file_paths_file", lambda *args: None)

        auto_add_untracked_files(show_progress=True)

        assert "Preparing 1000 untracked paths for review" in capsys.readouterr().err

    def test_auto_add_thousand_files_uses_one_discovery_add_and_write(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """The real 1,000-file path should remain constant in process topology."""
        from git_stage_batch.data import file_tracking

        ensure_state_directory_exists()
        generated = temp_git_repo / "generated"
        generated.mkdir()
        for index in range(1_000):
            (generated / f"file-{index}.txt").write_text("generated\n")

        discovery_calls = 0
        add_calls = 0
        write_calls = 0
        original_discovery = file_tracking.run_git_command
        original_add = file_tracking.git_add_paths_from_stdin
        original_write = file_tracking.write_file_paths_file

        def recording_discovery(*args, **kwargs):
            nonlocal discovery_calls
            discovery_calls += 1
            return original_discovery(*args, **kwargs)

        def recording_add(*args, **kwargs):
            nonlocal add_calls
            add_calls += 1
            return original_add(*args, **kwargs)

        def recording_write(*args, **kwargs):
            nonlocal write_calls
            write_calls += 1
            return original_write(*args, **kwargs)

        monkeypatch.setattr(file_tracking, "run_git_command", recording_discovery)
        monkeypatch.setattr(file_tracking, "git_add_paths_from_stdin", recording_add)
        monkeypatch.setattr(file_tracking, "write_file_paths_file", recording_write)

        auto_add_untracked_files()

        assert (discovery_calls, add_calls, write_calls) == (1, 1, 1)
        assert len(read_file_paths_file(get_auto_added_files_file_path())) == 1_000

    def test_auto_add_retries_paths_removed_during_scan(self, temp_git_repo, monkeypatch):
        """A disappearing candidate should be dropped by one refreshed retry."""
        from git_stage_batch.data import file_tracking

        discoveries = iter((["gone.txt", "kept.txt"], ["kept.txt"]))
        add_calls = []

        monkeypatch.setattr(
            file_tracking,
            "list_untracked_files",
            lambda paths=None: list(next(discoveries)),
        )
        monkeypatch.setattr(
            file_tracking,
            "_embedded_git_repository_index_path",
            lambda path: None,
        )

        def fake_add(paths, *, intent_to_add=False, check=True):
            add_calls.append(tuple(paths))
            return subprocess.CompletedProcess(
                ["git", "add"],
                1 if len(add_calls) == 1 else 0,
                "",
                "",
            )

        monkeypatch.setattr(file_tracking, "git_add_paths_from_stdin", fake_add)
        auto_add_untracked_files()

        assert add_calls == [("gone.txt", "kept.txt"), ("kept.txt",)]
        assert read_file_paths_file(get_auto_added_files_file_path()) == ["kept.txt"]

    def test_auto_add_rolls_back_manifest_when_index_update_fails(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """A failed initial and retry update should leave no declared transition."""
        from git_stage_batch.data import file_tracking

        (temp_git_repo / "failed.txt").write_text("content\n")
        monkeypatch.setattr(
            file_tracking,
            "git_add_paths_from_stdin",
            lambda paths, **kwargs: subprocess.CompletedProcess(
                ["git", "add"],
                1,
                "",
                "failed",
            ),
        )
        auto_add_untracked_files()

        assert not get_auto_added_files_file_path().exists()

    def test_auto_add_does_not_touch_index_when_manifest_write_fails(
        self,
        temp_git_repo,
        monkeypatch,
    ):
        """Manifest persistence must succeed before the bulk index transition."""
        from git_stage_batch.data import file_tracking

        (temp_git_repo / "private.txt").write_text("content\n")
        add_calls = []

        def fail_manifest_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(
            file_tracking,
            "write_file_paths_file",
            fail_manifest_write,
        )
        monkeypatch.setattr(
            file_tracking,
            "git_add_paths_from_stdin",
            lambda *args, **kwargs: add_calls.append(args),
        )

        with pytest.raises(OSError, match="disk full"):
            auto_add_untracked_files()

        assert add_calls == []
        result = subprocess.run(
            ["git", "ls-files", "--", "private.txt"],
            check=True,
            cwd=temp_git_repo,
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
