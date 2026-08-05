"""Tests for selected-line batch command parsing."""

from unittest.mock import Mock

import pytest

from git_stage_batch.commands.selection.batch_line_selection import (
    select_lines_for_batch_action,
)
from git_stage_batch.core.models import LineLevelChange
from git_stage_batch.exceptions import CommandError


def test_malformed_batch_line_selection_becomes_command_error():
    """Malformed batch selections should remain inside the command boundary."""
    line_changes = Mock(spec=LineLevelChange)

    with pytest.raises(CommandError, match="Invalid line ID: abc"):
        select_lines_for_batch_action(line_changes, "abc")
