"""Tests for CLI command dispatch."""

import argparse

import pytest

from git_stage_batch.cli.dispatch import dispatch_args
from git_stage_batch.exceptions import CommandError


def test_dispatch_args_no_command():
    """Test dispatch with no command raises CommandError."""
    args = argparse.Namespace(command=None)
    with pytest.raises(CommandError) as exc_info:
        dispatch_args(args)
    assert "No batch staging session in progress" in exc_info.value.message


def test_dispatch_args_callable():
    """Test that dispatch_args is callable."""
    assert dispatch_args is not None
    assert callable(dispatch_args)


def test_dispatch_args_with_command():
    """Test dispatch executes command function."""
    executed = []

    def mock_command(args):
        executed.append(True)

    args = argparse.Namespace(command="test", func=mock_command)
    dispatch_args(args)
    assert executed == [True]
