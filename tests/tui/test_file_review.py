"""Tests for TUI file review mode."""

from unittest.mock import patch

import pytest

from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.exceptions import BypassRefresh
from git_stage_batch.tui.file_review import handle_current_file_review
from git_stage_batch.tui.flow import FlowLocation, FlowState


def _line_changes(path: str = "test.txt") -> LineLevelChange:
    return LineLevelChange(
        path=path,
        header=HunkHeader(old_start=1, old_len=1, new_start=1, new_len=1),
        lines=[
            LineEntry(
                id=1,
                kind="+",
                old_line_number=None,
                new_line_number=1,
                text_bytes=b"test\n",
                text="test\n",
            )
        ],
    )


class TestHandleCurrentFileReview:
    """Tests for current-file review routing."""

    def test_current_file_review_renders_selected_file(self):
        """Test review opens the selected file through the show command."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch(
                    "git_stage_batch.commands.show.command_show",
                ) as mock_show:
                    with patch("builtins.input", return_value="q"):
                        with pytest.raises(BypassRefresh):
                            handle_current_file_review(flow_state)

        mock_show.assert_called_once_with(
            file="test.txt",
            page=None,
            selectable=True,
        )

    def test_current_file_review_updates_page_spec(self):
        """Test review can request an explicit file-review page."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show.command_show") as mock_show:
                    with patch("builtins.input", side_effect=["g", "2", "q"]):
                        with pytest.raises(BypassRefresh):
                            handle_current_file_review(flow_state)

        assert mock_show.call_args_list[0].kwargs["page"] is None
        assert mock_show.call_args_list[1].kwargs["page"] == "2"

    def test_current_file_review_routes_line_include(self):
        """Test line include acts on file-review line IDs."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show.command_show"):
                    with patch(
                        "git_stage_batch.commands.include.command_include_line"
                    ) as mock_include:
                        with patch("builtins.input", side_effect=["i", "1,3", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_include.assert_called_once_with(
            "1,3",
            file="test.txt",
            auto_advance=False,
        )

    def test_current_file_review_routes_file_skip(self):
        """Test file skip acts on the reviewed file."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show.command_show"):
                    with patch(
                        "git_stage_batch.commands.skip.command_skip_file"
                    ) as mock_skip:
                        with patch("builtins.input", side_effect=["S", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_skip.assert_called_once_with(
            "test.txt",
            quiet=True,
            advance=False,
            auto_advance=False,
        )

    def test_batch_source_renders_batch_file(self):
        """Test batch source review uses the batch show command."""
        flow_state = FlowState(
            source=FlowLocation.for_batch("scratch"),
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch(
                    "git_stage_batch.commands.show_from.command_show_from_batch"
                ) as mock_show:
                    with patch("builtins.input", return_value="q"):
                        with pytest.raises(BypassRefresh):
                            handle_current_file_review(flow_state)

        mock_show.assert_called_once_with(
            "scratch",
            file="test.txt",
            page=None,
            selectable=True,
        )

    def test_batch_source_disables_skip(self, capsys):
        """Test skip is not routed when reviewing a batch source."""
        flow_state = FlowState(
            source=FlowLocation.for_batch("scratch"),
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("test.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show_from.command_show_from_batch"):
                    with patch(
                        "git_stage_batch.commands.skip.command_skip_line"
                    ) as mock_skip:
                        with patch("builtins.input", side_effect=["s", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        assert "Skip is not available" in capsys.readouterr().err
        mock_skip.assert_not_called()
