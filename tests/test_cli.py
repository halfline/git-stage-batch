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

    assert result.returncode == 1
    assert "No batch staging session in progress" in result.stderr


def test_cli_question_mark_shows_help():
    """Test that ? shortcut shows help via parse_command_line."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "?"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "git-stage-batch" in result.stdout
    # Either shows man page (NAME/SYNOPSIS) or argparse help (usage:)
    assert ("NAME" in result.stdout and "SYNOPSIS" in result.stdout) or "usage:" in result.stdout
