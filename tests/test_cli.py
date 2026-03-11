"""Tests for command-line interface."""

import subprocess
import sys


def test_cli_version():
    """Test that --version flag works."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "--version"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "git-stage-batch" in result.stdout
    assert "0.1.0" in result.stdout


def test_cli_help():
    """Test that --help flag works (shows either man page or argparse help)."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "git-stage-batch" in result.stdout
    # Either shows man page (NAME/SYNOPSIS) or argparse help (usage:)
    assert ("NAME" in result.stdout and "SYNOPSIS" in result.stdout) or "usage:" in result.stdout


def test_cli_help_short():
    """Test that -h flag works (shows either man page or argparse help)."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "-h"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Either shows man page (NAME/SYNOPSIS) or argparse help (usage:)
    assert ("NAME" in result.stdout and "SYNOPSIS" in result.stdout) or "usage:" in result.stdout


def test_cli_no_args_shows_error(tmp_path, monkeypatch):
    """Test that running with no arguments shows helpful error."""
    # Change to a non-git directory
    monkeypatch.chdir(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 128  # Git's exit code for "not a git repository"
    assert "Not inside a git repository" in result.stderr


def test_cli_no_args_defaults_to_include_during_session(tmp_path, monkeypatch):
    """Test that running with no args defaults to include when session is active."""
    import subprocess

    # Create a temp git repo
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True, cwd=repo, capture_output=True)

    # Create initial commit
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "README.md"], check=True, cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], check=True, cwd=repo, capture_output=True)

    # Change to the repo directory
    monkeypatch.chdir(repo)

    # Modify a file before starting session
    (repo / "README.md").write_text("# Test\nNew content\n")

    # Start a session
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "start"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    # Run with no args - should default to include
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Hunk staged" in result.stdout

    # Verify the hunk was staged
    result = subprocess.run(
        ["git", "diff", "--cached"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert "+New content" in result.stdout
