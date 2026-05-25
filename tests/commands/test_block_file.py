"""Tests for block-file command."""

import subprocess

import pytest

from git_stage_batch.commands.block_file import command_block_file
from git_stage_batch.exceptions import CommandError
from git_stage_batch.utils.file_io import read_file_paths_file
from git_stage_batch.utils.git import get_gitignore_path, get_local_exclude_path
from git_stage_batch.utils.paths import get_blocked_files_file_path


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


class TestCommandBlockFile:
    """Tests for block-file command."""

    def test_block_file_requires_argument(self, temp_git_repo):
        """Test that block-file requires a file path argument."""
        with pytest.raises(CommandError):
            command_block_file("")

    def test_block_file_adds_to_gitignore(self, temp_git_repo, capsys):
        """Test that block-file adds file to .gitignore."""
        # Create untracked file
        (temp_git_repo / "unwanted.txt").write_text("ignore me\n")

        # Block the file
        command_block_file("unwanted.txt")

        # Check .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "unwanted.txt\n" in content

        captured = capsys.readouterr()
        assert "Blocked file: unwanted.txt" in captured.err

    def test_block_file_adds_to_blocked_list(self, temp_git_repo):
        """Test that block-file adds file to blocked-files state."""
        # Create untracked file
        (temp_git_repo / "blocked.txt").write_text("blocked content\n")

        # Block it
        command_block_file("blocked.txt")

        # Verify it's in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "blocked.txt" in blocked

    def test_block_file_resolves_absolute_path(self, temp_git_repo):
        """Test that block-file resolves absolute paths to repo-relative."""
        # Create untracked file
        (temp_git_repo / "file.txt").write_text("content\n")

        # Block using absolute path
        abs_path = str(temp_git_repo / "file.txt")
        command_block_file(abs_path)

        # Should be stored as relative path
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "file.txt" in blocked

        # .gitignore should have relative path
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "file.txt\n" in content

    def test_block_file_no_duplicates_in_gitignore(self, temp_git_repo):
        """Test that blocking the same file twice doesn't duplicate entries."""
        (temp_git_repo / "dup.txt").write_text("content\n")

        # Block twice
        command_block_file("dup.txt")
        command_block_file("dup.txt")

        # Should only appear once in .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert content.count("dup.txt") == 1

    def test_block_file_no_duplicates_in_blocked_list(self, temp_git_repo):
        """Test that blocking the same file twice doesn't duplicate in blocked list."""
        (temp_git_repo / "dup.txt").write_text("content\n")

        # Block twice
        command_block_file("dup.txt")
        command_block_file("dup.txt")

        # Should only appear once in blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert blocked.count("dup.txt") == 1

    def test_block_file_removes_intent_to_add_from_index(self, temp_git_repo):
        """Test that block-file removes an intent-to-add file from the index during a session."""
        from git_stage_batch.commands.start import command_start

        # Create untracked file
        (temp_git_repo / "build_output.dat").write_text("binary data\n")

        # Start a session (auto-adds untracked files with intent-to-add)
        command_start()

        # Verify it's in the index
        result = subprocess.run(["git", "ls-files", "--", "build_output.dat"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "build_output.dat" in result.stdout

        # Block the file
        command_block_file("build_output.dat")

        # Should be removed from index
        result = subprocess.run(["git", "ls-files", "--", "build_output.dat"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "build_output.dat" not in result.stdout

        # Should not appear in git status
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "build_output.dat" not in result.stdout

    def test_block_file_preserves_tracked_file_in_index(self, temp_git_repo):
        """Test that block-file does not remove tracked files from the index during a session."""
        from git_stage_batch.commands.start import command_start

        # Modify a tracked file so it appears in the session.
        (temp_git_repo / "README.md").write_text("# Test\n\nChanged\n")

        command_start()
        command_block_file("README.md")

        result = subprocess.run(["git", "ls-files", "--stage", "--", "README.md"], capture_output=True, text=True, cwd=temp_git_repo)
        assert "README.md" in result.stdout

        result = subprocess.run(["git", "status", "--porcelain", "--", "README.md"], capture_output=True, text=True, cwd=temp_git_repo)
        assert result.stdout.startswith(" M ")

    def test_block_file_shows_next_hunk_during_session(self, temp_git_repo, capsys):
        """Test that block-file shows the next hunk when a session is active."""
        from git_stage_batch.commands.start import command_start

        # Create two untracked files so there's a next hunk after blocking
        (temp_git_repo / "unwanted.txt").write_text("ignore me\n")
        (temp_git_repo / "wanted.txt").write_text("keep me\n")

        # Start a session
        command_start()
        capsys.readouterr()

        # Block whichever file is shown first
        command_block_file("unwanted.txt")
        captured = capsys.readouterr()

        assert "Blocked file: unwanted.txt" in captured.err
        # Should show the next hunk (wanted.txt) after blocking
        assert "wanted.txt" in captured.out

    def test_block_file_with_subdirectory(self, temp_git_repo):
        """Test blocking a file in a subdirectory."""
        # Create subdirectory and file
        subdir = temp_git_repo / "src"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content\n")

        # Block it
        command_block_file("src/file.txt")

        # Check blocked list
        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "src/file.txt" in blocked

        # Check .gitignore
        gitignore = get_gitignore_path()
        content = gitignore.read_text()
        assert "src/file.txt\n" in content

    def test_block_file_local_only_adds_to_exclude(self, temp_git_repo, capsys):
        """Test that --local-only adds file to .git/info/exclude, not .gitignore."""
        (temp_git_repo / "local.txt").write_text("local content\n")

        command_block_file("local.txt", local_only=True)

        exclude = get_local_exclude_path()
        assert exclude.exists()
        assert "local.txt\n" in exclude.read_text()

        gitignore = get_gitignore_path()
        assert not gitignore.exists() or "local.txt" not in gitignore.read_text()

        captured = capsys.readouterr()
        assert "Blocked file: local.txt" in captured.err

    def test_block_file_local_only_adds_to_blocked_list(self, temp_git_repo):
        """Test that --local-only still adds file to the blocked list."""
        (temp_git_repo / "local.txt").write_text("local content\n")

        command_block_file("local.txt", local_only=True)

        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "local.txt" in blocked

    def test_block_file_local_only_no_duplicates_in_exclude(self, temp_git_repo):
        """Test that --local-only blocking the same file twice doesn't duplicate entries."""
        (temp_git_repo / "dup.txt").write_text("content\n")

        command_block_file("dup.txt", local_only=True)
        command_block_file("dup.txt", local_only=True)

        exclude = get_local_exclude_path()
        assert exclude.read_text().count("dup.txt") == 1

    def test_block_file_directory_with_trailing_slash(self, temp_git_repo):
        """Test that a directory argument with trailing slash is stored as dir/."""
        subdir = temp_git_repo / "build"
        subdir.mkdir()
        (subdir / "output.o").write_text("binary\n")

        command_block_file("build/")

        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "build/" in blocked

        gitignore = get_gitignore_path()
        assert "build/\n" in gitignore.read_text()

    def test_block_file_directory_without_trailing_slash(self, temp_git_repo):
        """Test that a bare directory name is normalized to dir/ form."""
        subdir = temp_git_repo / "dist"
        subdir.mkdir()
        (subdir / "bundle.js").write_text("code\n")

        command_block_file("dist")

        blocked = read_file_paths_file(get_blocked_files_file_path())
        assert "dist/" in blocked
        assert "dist" not in blocked

    def test_block_file_directory_prefix_match(self, temp_git_repo):
        """Test that blocking a directory suppresses files under it during a session."""
        from git_stage_batch.commands.start import command_start
        from git_stage_batch.utils.file_io import is_path_blocked

        subdir = temp_git_repo / "generated"
        subdir.mkdir()
        (subdir / "foo.c").write_text("code\n")
        (subdir / "bar.c").write_text("code\n")

        command_start()
        command_block_file("generated/")

        blocked = read_file_paths_file(get_blocked_files_file_path())
        blocked_set = set(blocked)
        assert is_path_blocked("generated/foo.c", blocked_set)
        assert is_path_blocked("generated/bar.c", blocked_set)
        assert not is_path_blocked("other.c", blocked_set)
