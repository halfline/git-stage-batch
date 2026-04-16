"""Tests for journal logging."""

import json
import subprocess
from pathlib import Path

import pytest

from git_stage_batch.commands.start import command_start
from git_stage_batch.utils.journal import log_journal
from git_stage_batch.utils.paths import get_state_directory_path


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(["git", "commit", "-m", "initial"], check=True, capture_output=True)

    return tmp_path


class TestJournal:
    """Tests for journal logging."""

    def test_journal_always_logs_to_session(self, temp_git_repo):
        """Test that journal always logs to session journal."""
        # Make changes for start
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        state_dir = get_state_directory_path()
        journal_file = state_dir / "journal.jsonl"

        # Session journal should exist
        assert journal_file.exists(), "Session journal should be created"

        # Should have content
        content = journal_file.read_text()
        assert content, "Session journal should have content"

        # Should be valid JSON lines
        lines = [line for line in content.strip().split('\n') if line]
        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "operation" in entry

    def test_journal_global_only_with_debug_env(self, temp_git_repo, monkeypatch):
        """Test that global journal only logs when GIT_STAGE_BATCH_DEBUG is set."""
        from git_stage_batch.utils.paths import ensure_state_directory_exists

        global_journal_path = Path("/var/tmp/git-stage-batch-journal.jsonl")

        # Ensure state directory exists
        ensure_state_directory_exists()

        # Clear any existing global journal
        if global_journal_path.exists():
            global_journal_path.unlink()

        # Without debug env var, global journal is not created.
        monkeypatch.delenv("GIT_STAGE_BATCH_DEBUG", raising=False)

        # Just log directly (don't need start)
        log_journal("test_operation_no_debug", test=True)

        # Global journal should not be created.
        assert not global_journal_path.exists() or "test_operation_no_debug" not in global_journal_path.read_text()

        # Test 2: With debug env var, should create global journal
        monkeypatch.setenv("GIT_STAGE_BATCH_DEBUG", "1")

        # Clear global journal again
        if global_journal_path.exists():
            global_journal_path.unlink()

        log_journal("test_operation_with_debug", test=True)

        # Global journal should now exist and have content
        assert global_journal_path.exists(), "Global journal should be created with debug env"

        content = global_journal_path.read_text()
        assert "test_operation_with_debug" in content

        # Clean up
        if global_journal_path.exists():
            global_journal_path.unlink()

    def test_journal_entries_have_required_fields(self, temp_git_repo):
        """Test that journal entries have all required fields."""
        # Make changes for start
        (temp_git_repo / "README.md").write_text("# Test\nmodified\n")
        command_start()

        state_dir = get_state_directory_path()
        journal_file = state_dir / "journal.jsonl"

        content = journal_file.read_text()
        lines = [line for line in content.strip().split('\n') if line]

        # Check first entry
        entry = json.loads(lines[0])

        # Required fields
        assert "timestamp" in entry
        assert "pid" in entry
        assert "operation" in entry
        assert "stack" in entry

        # Timestamp should be ISO format
        assert "T" in entry["timestamp"]

        # PID should be positive integer
        assert entry["pid"] > 0

        # Stack should be a list
        assert isinstance(entry["stack"], list)

    def test_journal_global_includes_repo_path(self, temp_git_repo, monkeypatch):
        """Test that global journal entries include repo path."""
        monkeypatch.setenv("GIT_STAGE_BATCH_DEBUG", "1")

        global_journal_path = Path("/var/tmp/git-stage-batch-journal.jsonl")

        # Clear existing
        if global_journal_path.exists():
            global_journal_path.unlink()

        log_journal("test_repo_path", test=True)

        # Read global journal
        assert global_journal_path.exists()
        content = global_journal_path.read_text()

        entry = json.loads(content.strip().split('\n')[-1])

        # Should have repo field
        assert "repo" in entry
        assert entry["repo"] is not None
        assert str(temp_git_repo) in entry["repo"]

        # Clean up
        global_journal_path.unlink()

    def test_journal_logging_never_breaks_operations(self, temp_git_repo, monkeypatch):
        """Test that journal logging failures don't break operations."""
        from git_stage_batch.utils.paths import ensure_state_directory_exists

        # Ensure state directory exists first
        ensure_state_directory_exists()

        # Make journal path unwritable (this will cause journal to fail)
        state_dir = get_state_directory_path()
        journal_file = state_dir / "journal.jsonl"

        # Create journal as a directory (will cause write to fail)
        if journal_file.exists():
            journal_file.unlink()
        journal_file.mkdir()

        # This should not raise an exception.
        try:
            log_journal("test_operation", test=True)
        except Exception as e:
            pytest.fail(f"Journal logging failure should not raise exception: {e}")

        # Clean up
        if journal_file.exists() and journal_file.is_dir():
            journal_file.rmdir()
