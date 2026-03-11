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
    """Test that --help flag works."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "git-stage-batch" in result.stdout
    assert "--version" in result.stdout


def test_cli_help_short():
    """Test that -h flag works."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli", "-h"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_cli_no_args_succeeds():
    """Test that running with no arguments succeeds."""
    result = subprocess.run(
        [sys.executable, "-m", "git_stage_batch.cli"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
