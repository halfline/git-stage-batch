"""Tests for the TUI fixup submenu."""

from unittest.mock import patch

from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.tui.fixup_menu import handle_fixup_menu


def _line_changes() -> LineLevelChange:
    header = HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1)
    return LineLevelChange(
        path="test.txt",
        header=header,
        lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"test\n",
                text="test\n",
            ),
        ],
    )


def test_handle_fixup_menu_cancel_clears_state(capsys):
    """Test canceling the fixup menu clears persisted iteration state."""
    with patch("git_stage_batch.tui.fixup_menu.Colors.enabled", return_value=False):
        with patch(
            "git_stage_batch.tui.fixup_menu.load_line_changes_from_state",
            return_value=_line_changes(),
        ):
            with patch(
                "git_stage_batch.tui.fixup_menu.command_suggest_fixup"
            ) as mock_fixup:
                with patch(
                    "git_stage_batch.tui.fixup_menu.prompt_fixup_action",
                    return_value="q",
                ):
                    with patch(
                        "git_stage_batch.tui.fixup_menu.clear_suggest_fixup_state"
                    ) as mock_clear:
                        handle_fixup_menu()

    mock_fixup.assert_called_once_with()
    mock_clear.assert_called_once_with()
    assert "Canceled." in capsys.readouterr().out
