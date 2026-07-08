"""Tests for replacement-selection command helpers."""

import pytest

from git_stage_batch.commands.selection.replacement_selection import (
    require_contiguous_display_selection,
)
from git_stage_batch.exceptions import CommandError


def test_contiguous_display_selection_accepts_adjacent_ids():
    """Adjacent selected display IDs should pass replacement validation."""
    require_contiguous_display_selection({2, 3, 4})


def test_contiguous_display_selection_rejects_gapped_ids():
    """Gapped selected display IDs should fail replacement validation."""
    with pytest.raises(CommandError) as exc_info:
        require_contiguous_display_selection({2, 4})

    assert "Replacement selection must be one contiguous line range." in (
        exc_info.value.message
    )
