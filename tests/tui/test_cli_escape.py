"""Tests for the TUI CLI command escape."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from git_stage_batch.exceptions import BypassRefresh
from git_stage_batch.tui.cli_escape import handle_cli_escape


def test_handle_cli_escape_executes_parsed_args():
    """Test parsed CLI args execute through the escape adapter."""
    args = SimpleNamespace(
        interactive_flag=False,
        interactive_command=False,
    )
    print_help = Mock()

    with patch(
        "git_stage_batch.tui.cli_escape.parse_command_line",
        return_value=args,
    ) as mock_parse:
        with patch(
            "git_stage_batch.tui.cli_escape.execute_non_interactive_args"
        ) as mock_execute:
            with pytest.raises(BypassRefresh):
                handle_cli_escape("status", print_help=print_help)

    mock_parse.assert_called_once_with(["status"], quiet=False)
    mock_execute.assert_called_once_with(args)
    print_help.assert_not_called()
