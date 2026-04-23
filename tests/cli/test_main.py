"""Tests for CLI entry point."""

import sys
from argparse import Namespace
from importlib import import_module
from unittest.mock import patch

import pytest

main_module = import_module("git_stage_batch.cli.main")


def test_main_callable():
    """Test that main is callable."""
    assert main_module.main is not None
    assert callable(main_module.main)


def test_main_with_no_args():
    """Test main with no arguments exits with error."""
    with patch.object(sys, 'argv', ['git-stage-batch']):
        with patch.object(main_module, "dispatch_args", side_effect=main_module.CommandError("boom", exit_code=1)):
            with patch.object(main_module, "parse_command_line", return_value=Namespace(working_directory=None)):
                with pytest.raises(SystemExit) as exc_info:
                    main_module.main()
                assert exc_info.value.code == 1
