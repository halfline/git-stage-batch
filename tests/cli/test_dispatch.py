"""Tests for CLI command dispatch."""

import argparse

from git_stage_batch.cli.dispatch import dispatch_args


def test_dispatch_args_no_command():
    """Test dispatch with no command (current behavior is pass)."""
    args = argparse.Namespace()
    # Should not raise an error
    dispatch_args(args)


def test_dispatch_args_callable():
    """Test that dispatch_args is callable."""
    assert dispatch_args is not None
    assert callable(dispatch_args)
