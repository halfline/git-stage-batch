"""Tests for CLI entry point."""

import sys
from unittest.mock import patch

from git_stage_batch.cli.main import main


def test_main_callable():
    """Test that main is callable."""
    assert main is not None
    assert callable(main)


def test_main_with_no_args():
    """Test main with no arguments."""
    with patch.object(sys, 'argv', ['git-stage-batch']):
        # Should not raise an error
        try:
            main()
        except SystemExit:
            # May exit on certain arg parsing scenarios
            pass
