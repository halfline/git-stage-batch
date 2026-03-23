"""Tests for CLI entry point."""

import sys
from unittest.mock import patch

import pytest

from git_stage_batch.cli.main import main
from git_stage_batch.exceptions import CommandError


def test_main_callable():
    """Test that main is callable."""
    assert main is not None
    assert callable(main)


def test_main_with_no_args():
    """Test main with no arguments raises CommandError."""
    with patch.object(sys, 'argv', ['git-stage-batch']):
        with pytest.raises(CommandError) as exc_info:
            main()
        assert "No batch staging session in progress" in exc_info.value.message
