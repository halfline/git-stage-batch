"""Tests for session batch source management."""

from git_stage_batch.data.session import initialize_abort_state
from git_stage_batch.utils.paths import (
    get_abort_head_file_path,
    get_abort_stash_file_path,
)
from git_stage_batch.utils.file_io import write_text_file_contents
from git_stage_batch.data.batch_sources import (
    get_saved_session_file_content,
    load_saved_session_file_as_buffer,
)
from git_stage_batch.utils.paths import (
    get_abort_snapshot_list_file_path,
    get_abort_snapshots_directory_path,
)
from git_stage_batch.utils.file_io import write_file_paths_file
from git_stage_batch.exceptions import CommandError
from git_stage_batch.data.batch_sources import create_batch_source_commit
from git_stage_batch.utils.file_io import read_text_file_contents
from git_stage_batch.data.batch_sources import load_session_batch_sources
from git_stage_batch.data.batch_sources import save_session_batch_sources
from git_stage_batch.data.batch_sources import (
    get_batch_source_for_file
)

import subprocess

import pytest


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


@pytest.fixture
def session_with_stash(temp_git_repo):
    """Set up a session with abort state including stash."""

    # Initialize abort state
    initialize_abort_state()

    # Create and modify a tracked file
    test_file = temp_git_repo / "test.txt"
    test_file.write_text("original content\n")
    subprocess.run(["git", "add", "test.txt"], check=True, cwd=temp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Add test file"], check=True, cwd=temp_git_repo, capture_output=True)

    # Get selected HEAD
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True
    )
    selected_head = head_result.stdout.strip()

    # Modify the file (this would be the working tree state at session start)
    test_file.write_text("modified content\n")

    # Create a stash to simulate session start state
    stash_result = subprocess.run(
        ["git", "stash", "create"],
        check=True,
        cwd=temp_git_repo,
        capture_output=True,
        text=True
    )
    stash_commit = stash_result.stdout.strip()

    # Write abort state
    write_text_file_contents(get_abort_head_file_path(), selected_head + "\n")
    write_text_file_contents(get_abort_stash_file_path(), stash_commit + "\n")

    return temp_git_repo


class TestGetSavedSessionFileContent:
    """Tests for get_saved_session_file_content function."""

    def test_retrieves_tracked_file_from_stash(self, session_with_stash):
        """Test retrieving tracked file buffers from stash."""

        content = get_saved_session_file_content("test.txt")
        assert content == b"modified content\n"

    def test_loads_tracked_file_from_stash_as_buffer(self, session_with_stash):
        """Session-start buffers can be loaded without returning bytes."""

        with load_saved_session_file_as_buffer("test.txt") as buffer:
            assert buffer.byte_count == len(b"modified content\n")
            assert list(buffer.byte_chunks(9)) == [b"modified ", b"content\n"]
            assert buffer[0] == b"modified content\n"

    def test_retrieves_untracked_file_from_snapshot(self, temp_git_repo):
        """Test retrieving untracked file buffers from snapshot."""

        # Initialize session
        initialize_abort_state()

        # Create snapshot for untracked file
        snapshot_dir = get_abort_snapshots_directory_path()
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshot_dir / "untracked.txt"
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        write_text_file_contents(snapshot_file, "untracked content\n")

        # Record in snapshot list
        write_file_paths_file(get_abort_snapshot_list_file_path(), ["untracked.txt"])

        content = get_saved_session_file_content("untracked.txt")
        assert content == b"untracked content\n"

    def test_preserves_exact_bytes(self, temp_git_repo):
        """Test that exact byte content is preserved (no normalization)."""

        # Initialize session
        initialize_abort_state()

        # Create snapshot with mixed line endings
        snapshot_dir = get_abort_snapshots_directory_path()
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_file = snapshot_dir / "mixed.txt"
        snapshot_file.parent.mkdir(parents=True, exist_ok=True)
        # Write with binary mode to preserve \r\n
        snapshot_file.write_bytes(b"line1\r\nline2\rline3\n")

        # Record in snapshot list
        write_file_paths_file(get_abort_snapshot_list_file_path(), ["mixed.txt"])

        content = get_saved_session_file_content("mixed.txt")
        # Exact bytes should be preserved (no line ending normalization)
        assert content == b"line1\r\nline2\rline3\n"

    def test_raises_error_for_missing_session(self, temp_git_repo):
        """Test that error is raised when no session exists."""

        with pytest.raises(CommandError, match="No session found"):
            get_saved_session_file_content("test.txt")


class TestCreateBatchSourceCommit:
    """Tests for create_batch_source_commit function."""

    def test_creates_commit_for_tracked_file(self, session_with_stash):
        """Test creating batch source commit for tracked file."""

        commit_sha = create_batch_source_commit("test.txt")

        # Verify it's a valid commit SHA
        assert len(commit_sha) == 40
        assert commit_sha.isalnum()

        # Verify we can read the file from the commit
        result = subprocess.run(
            ["git", "show", f"{commit_sha}:test.txt"],
            capture_output=True,
            text=True
        )
        assert result.returncode == 0
        assert result.stdout == "modified content\n"

    def test_batch_source_has_baseline_as_parent(self, session_with_stash):
        """Test that batch source commit has baseline as parent."""

        baseline = read_text_file_contents(get_abort_head_file_path()).strip()
        commit_sha = create_batch_source_commit("test.txt")

        # Get parent of batch source commit
        result = subprocess.run(
            ["git", "rev-parse", f"{commit_sha}^"],
            capture_output=True,
            text=True
        )
        parent = result.stdout.strip()
        assert parent == baseline


class TestBatchSourceCache:
    """Tests for batch source cache functions."""

    def test_load_empty_cache(self, temp_git_repo):
        """Test loading cache when file doesn't exist."""

        batch_sources = load_session_batch_sources()
        assert batch_sources == {}

    def test_save_and_load_cache(self, temp_git_repo):
        """Test saving and loading batch sources."""

        # Initialize session to create state directory
        initialize_abort_state()

        # Save batch sources
        test_data = {
            "file1.txt": "abc123",
            "file2.txt": "def456"
        }
        save_session_batch_sources(test_data)

        # Load and verify
        loaded = load_session_batch_sources()
        assert loaded == test_data

    def test_get_batch_source_for_file_existing(self, temp_git_repo):
        """Test getting batch source for file that exists in cache."""

        # Initialize session
        initialize_abort_state()

        # Save batch sources
        save_session_batch_sources({"test.txt": "abc123"})

        # Retrieve
        batch_source = get_batch_source_for_file("test.txt")
        assert batch_source == "abc123"

    def test_get_batch_source_for_file_nonexistent(self, temp_git_repo):
        """Test getting batch source for file that doesn't exist in cache."""

        # Initialize session
        initialize_abort_state()

        # Retrieve non-existent file
        batch_source = get_batch_source_for_file("nonexistent.txt")
        assert batch_source is None
