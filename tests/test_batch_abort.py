"""Tests for batch abort and restore integration."""

import subprocess

import pytest

from git_stage_batch.state import run_git_command


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary git repository for testing."""
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    # Create initial commit
    (tmp_path / "README").write_text("initial\n")
    subprocess.run(["git", "add", "README"], check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], check=True, capture_output=True)

    return tmp_path


def test_abort_drops_batches_created_during_session(temp_git_repo):
    """Test that abort drops batches created during session."""
    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Create batch during session
    subprocess.run(["git-stage-batch", "new", "session-batch"], capture_output=True)

    # Verify batch exists
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/session-batch"], check=False)
    assert ref_result.returncode == 0

    # Abort session
    subprocess.run(["git-stage-batch", "abort"], capture_output=True)

    # Verify batch was dropped
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/session-batch"], check=False)
    assert ref_result.returncode != 0


def test_abort_restores_dropped_batches(temp_git_repo):
    """Test that abort restores batches dropped during session."""
    # Create batch before session
    subprocess.run(["git-stage-batch", "new", "existing-batch", "--note", "Pre-session"], capture_output=True)
    original_sha = run_git_command(["rev-parse", "refs/batches/existing-batch"]).stdout.strip()

    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Drop batch during session
    subprocess.run(["git-stage-batch", "drop", "existing-batch"], capture_output=True)

    # Verify batch is gone
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/existing-batch"], check=False)
    assert ref_result.returncode != 0

    # Abort session
    subprocess.run(["git-stage-batch", "abort"], capture_output=True)

    # Verify batch was restored
    ref_result = run_git_command(["show-ref", "--verify", "refs/batches/existing-batch"], check=False)
    assert ref_result.returncode == 0

    restored_sha = run_git_command(["rev-parse", "refs/batches/existing-batch"]).stdout.strip()
    assert restored_sha == original_sha


def test_abort_reverts_batch_mutations(temp_git_repo):
    """Test that abort reverts batch mutations to original state."""
    # Create batch before session
    subprocess.run(["git-stage-batch", "new", "test-batch"], capture_output=True)
    original_sha = run_git_command(["rev-parse", "refs/batches/test-batch"]).stdout.strip()

    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Mutate batch by creating a new commit
    # (This would normally happen through batch operations, simulate with git)
    (temp_git_repo / "newfile.txt").write_text("new\n")
    subprocess.run(["git", "add", "newfile.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "New commit"], check=True, capture_output=True)
    new_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/test-batch", new_sha])

    # Verify batch was mutated
    current_sha = run_git_command(["rev-parse", "refs/batches/test-batch"]).stdout.strip()
    assert current_sha != original_sha

    # Abort session
    subprocess.run(["git-stage-batch", "abort"], capture_output=True)

    # Verify batch was reverted
    restored_sha = run_git_command(["rev-parse", "refs/batches/test-batch"]).stdout.strip()
    assert restored_sha == original_sha


def test_abort_handles_mixed_batch_operations(temp_git_repo):
    """Test that abort handles created, dropped, and mutated batches together."""
    # Create batches before session
    subprocess.run(["git-stage-batch", "new", "to-drop"], capture_output=True)
    to_drop_sha = run_git_command(["rev-parse", "refs/batches/to-drop"]).stdout.strip()

    subprocess.run(["git-stage-batch", "new", "to-mutate"], capture_output=True)
    to_mutate_sha = run_git_command(["rev-parse", "refs/batches/to-mutate"]).stdout.strip()

    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Create new batch
    subprocess.run(["git-stage-batch", "new", "created"], capture_output=True)

    # Drop existing batch
    subprocess.run(["git-stage-batch", "drop", "to-drop"], capture_output=True)

    # Mutate existing batch
    (temp_git_repo / "newfile.txt").write_text("new\n")
    subprocess.run(["git", "add", "newfile.txt"], check=True)
    subprocess.run(["git", "commit", "-m", "New"], check=True, capture_output=True)
    new_sha = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    run_git_command(["update-ref", "refs/batches/to-mutate", new_sha])

    # Abort session
    subprocess.run(["git-stage-batch", "abort"], capture_output=True)

    # Verify: created batch dropped
    result = run_git_command(["show-ref", "--verify", "refs/batches/created"], check=False)
    assert result.returncode != 0

    # Verify: to-drop batch restored
    result = run_git_command(["show-ref", "--verify", "refs/batches/to-drop"], check=False)
    assert result.returncode == 0
    restored = run_git_command(["rev-parse", "refs/batches/to-drop"]).stdout.strip()
    assert restored == to_drop_sha

    # Verify: to-mutate batch reverted
    restored = run_git_command(["rev-parse", "refs/batches/to-mutate"]).stdout.strip()
    assert restored == to_mutate_sha


def test_stop_preserves_batches(temp_git_repo):
    """Test that stop command preserves batches (unlike abort)."""
    # Create batch
    subprocess.run(["git-stage-batch", "new", "test-batch"], capture_output=True)

    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Create another batch during session
    subprocess.run(["git-stage-batch", "new", "session-batch"], capture_output=True)

    # Stop session
    subprocess.run(["git-stage-batch", "stop"], capture_output=True)

    # Verify both batches still exist
    result1 = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert result1.returncode == 0

    result2 = run_git_command(["show-ref", "--verify", "refs/batches/session-batch"], check=False)
    assert result2.returncode == 0


def test_again_preserves_batches(temp_git_repo):
    """Test that again command preserves batches."""
    # Start session
    (temp_git_repo / "file.txt").write_text("content\n")
    subprocess.run(["git-stage-batch", "start"], capture_output=True)

    # Create batch
    subprocess.run(["git-stage-batch", "new", "test-batch"], capture_output=True)

    # Run again
    subprocess.run(["git-stage-batch", "again"], capture_output=True, input="\n", text=True)

    # Verify batch still exists
    result = run_git_command(["show-ref", "--verify", "refs/batches/test-batch"], check=False)
    assert result.returncode == 0
