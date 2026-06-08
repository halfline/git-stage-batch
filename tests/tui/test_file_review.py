"""Tests for TUI file review mode."""

from unittest.mock import patch

import pytest

from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.exceptions import BypassRefresh
from git_stage_batch.tui.file_review import ReviewFileEntry
from git_stage_batch.tui.file_review import handle_file_browser
from git_stage_batch.tui.file_review import handle_current_file_review
from git_stage_batch.tui.file_review import list_review_file_entries
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

    def test_current_file_review_routes_line_replacement_include(self):
        """Test replacement include acts on file-review line IDs."""
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
                        "git_stage_batch.commands.include.command_include_line_as"
                    ) as mock_include_as:
                        with patch(
                            "builtins.input",
                            side_effect=["r", "1-2", "replacement", "q"],
                        ):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_include_as.assert_called_once_with(
            "1-2",
            "replacement",
            file="test.txt",
            auto_advance=False,
        )

    def test_current_file_review_replacement_cancels_on_empty_text(self):
        """Test empty replacement text cancels the replacement action."""
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
                        "git_stage_batch.commands.include.command_include_line_as"
                    ) as mock_include_as:
                        with patch("builtins.input", side_effect=["r", "1", "", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_include_as.assert_not_called()

    def test_current_file_review_routes_line_replacement_to_batch(self):
        """Test replacement can be saved to a target batch."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.for_batch("scratch"),
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
                        "git_stage_batch.commands.discard.command_discard_line_as_to_batch"
                    ) as mock_discard_as:
                        with patch(
                            "builtins.input",
                            side_effect=["r", "1", "replacement", "q"],
                        ):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_discard_as.assert_called_once_with(
            "scratch",
            "1",
            "replacement",
            file="test.txt",
            quiet=True,
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

    def test_current_file_review_blocks_file_in_gitignore(self):
        """Test block action routes to block-file using gitignore."""
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
                        "git_stage_batch.tui.file_review.confirm_destructive_operation",
                        return_value=True,
                    ):
                        with patch(
                            "git_stage_batch.commands.block_file.command_block_file"
                        ) as mock_block:
                            with patch("builtins.input", side_effect=["B", "g", "q"]):
                                with pytest.raises(BypassRefresh):
                                    handle_current_file_review(flow_state)

        mock_block.assert_called_once_with("test.txt", local_only=False)

    def test_current_file_review_blocks_file_locally(self):
        """Test block action can target local exclude."""
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
                        "git_stage_batch.tui.file_review.confirm_destructive_operation",
                        return_value=True,
                    ):
                        with patch(
                            "git_stage_batch.commands.block_file.command_block_file"
                        ) as mock_block:
                            with patch("builtins.input", side_effect=["B", "l", "q"]):
                                with pytest.raises(BypassRefresh):
                                    handle_current_file_review(flow_state)

        mock_block.assert_called_once_with("test.txt", local_only=True)

    def test_current_file_review_block_cancelled_by_confirmation(self):
        """Test block action honors destructive confirmation."""
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
                        "git_stage_batch.tui.file_review.confirm_destructive_operation",
                        return_value=False,
                    ):
                        with patch(
                            "git_stage_batch.commands.block_file.command_block_file"
                        ) as mock_block:
                            with patch("builtins.input", side_effect=["B", "q"]):
                                with pytest.raises(BypassRefresh):
                                    handle_current_file_review(flow_state)

        mock_block.assert_not_called()

    def test_current_file_review_unblocks_file(self):
        """Test unblock action routes to unblock-file."""
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
                        "git_stage_batch.commands.unblock_file.command_unblock_file"
                    ) as mock_unblock:
                        with patch("builtins.input", side_effect=["U", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_unblock.assert_called_once_with("test.txt")

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

    def test_batch_source_routes_line_replacement_include(self):
        """Test batch replacement include is routed through include-from."""
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
                        "git_stage_batch.commands.include_from.command_include_from_batch"
                    ) as mock_include_from:
                        with patch(
                            "builtins.input",
                            side_effect=["r", "1", "replacement", "q"],
                        ):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        mock_include_from.assert_called_once_with(
            "scratch",
            line_ids="1",
            file="test.txt",
            replacement_text="replacement",
        )

    def test_batch_source_replacement_rejects_batch_target(self, capsys):
        """Test replacement refuses batch-to-batch transfer."""
        flow_state = FlowState(
            source=FlowLocation.for_batch("source"),
            target=FlowLocation.for_batch("target"),
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
                        "git_stage_batch.commands.include_from.command_include_from_batch"
                    ) as mock_include_from:
                        with patch(
                            "builtins.input",
                            side_effect=["r", "1", "replacement", "q"],
                        ):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        assert "Batch-to-batch transfers" in capsys.readouterr().err
        mock_include_from.assert_not_called()

    def test_current_file_review_opens_another_file(self):
        """Test review can switch to another listed file."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.load_line_changes_from_state",
            return_value=_line_changes("first.txt"),
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch(
                    "git_stage_batch.tui.file_review.list_review_file_entries",
                    return_value=[
                        ReviewFileEntry("first.txt"),
                        ReviewFileEntry("second.txt"),
                    ],
                ):
                    with patch("git_stage_batch.commands.show.command_show") as mock_show:
                        with patch("builtins.input", side_effect=["o", "2", "q"]):
                            with pytest.raises(BypassRefresh):
                                handle_current_file_review(flow_state)

        assert mock_show.call_args_list[0].kwargs["file"] == "first.txt"
        assert mock_show.call_args_list[1].kwargs["file"] == "second.txt"


class TestHandleFileBrowser:
    """Tests for review file chooser."""

    def test_file_browser_opens_selected_file(self):
        """Test file browser opens a selected entry in review mode."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.list_review_file_entries",
            return_value=[
                ReviewFileEntry("first.txt"),
                ReviewFileEntry("second.txt"),
            ],
        ):
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show.command_show") as mock_show:
                    with patch("builtins.input", side_effect=["2", "q"]):
                        with pytest.raises(BypassRefresh):
                            handle_file_browser(flow_state)

        mock_show.assert_called_once_with(
            file="second.txt",
            page=None,
            selectable=True,
        )

    def test_file_browser_filters_entries(self):
        """Test file browser accepts a pattern before choosing a file."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        entries_by_pattern = {
            None: [
                ReviewFileEntry("src/app.py"),
                ReviewFileEntry("README.md"),
            ],
            "src/**": [ReviewFileEntry("src/app.py")],
        }

        def list_entries(_flow_state, pattern=None):
            return entries_by_pattern[pattern]

        with patch(
            "git_stage_batch.tui.file_review.list_review_file_entries",
            side_effect=list_entries,
        ) as mock_list:
            with patch(
                "git_stage_batch.tui.file_review.get_hunk_counts",
                return_value={},
            ):
                with patch("git_stage_batch.commands.show.command_show") as mock_show:
                    with patch("builtins.input", side_effect=["/src/**", "1", "q"]):
                        with pytest.raises(BypassRefresh):
                            handle_file_browser(flow_state)

        assert mock_list.call_args_list[0].kwargs["pattern"] is None
        assert mock_list.call_args_list[1].kwargs["pattern"] == "src/**"
        mock_show.assert_called_once_with(
            file="src/app.py",
            page=None,
            selectable=True,
        )

    def test_file_browser_returns_when_no_entries(self, capsys):
        """Test file browser exits when no files are reviewable."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.list_review_file_entries",
            return_value=[],
        ):
            with pytest.raises(BypassRefresh):
                handle_file_browser(flow_state)

        assert "No files to review" in capsys.readouterr().out


class TestListReviewFileEntries:
    """Tests for file review entry groundwork."""

    def test_live_entries_include_changed_and_untracked_files(self):
        """Test live entries combine changed and untracked files."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.list_changed_files",
            return_value=["src/app.py", "README.md"],
        ):
            with patch(
                "git_stage_batch.tui.file_review.list_untracked_files",
                return_value=["notes.txt"],
            ):
                entries = list_review_file_entries(flow_state)

        assert [entry.path for entry in entries] == [
            "src/app.py",
            "README.md",
            "notes.txt",
        ]

    def test_live_entries_apply_pattern_filter(self):
        """Test live entries use gitignore-style filtering."""
        flow_state = FlowState(
            source=FlowLocation.WORKING_TREE,
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.list_changed_files",
            return_value=["src/app.py", "README.md"],
        ):
            with patch(
                "git_stage_batch.tui.file_review.list_untracked_files",
                return_value=[],
            ):
                with patch(
                    "git_stage_batch.tui.file_review.resolve_gitignore_style_patterns",
                    return_value=["src/app.py"],
                ) as mock_resolve:
                    entries = list_review_file_entries(flow_state, pattern="src/**")

        mock_resolve.assert_called_once_with(
            ["src/app.py", "README.md"],
            ["src/**"],
        )
        assert [entry.path for entry in entries] == ["src/app.py"]

    def test_batch_entries_read_batch_metadata(self):
        """Test batch entries come from batch metadata."""
        flow_state = FlowState(
            source=FlowLocation.for_batch("scratch"),
            target=FlowLocation.STAGING_AREA,
        )

        with patch(
            "git_stage_batch.tui.file_review.read_batch_metadata",
            return_value={
                "files": {
                    "src/app.py": {},
                    "README.md": {},
                }
            },
        ) as mock_read:
            entries = list_review_file_entries(flow_state)

        mock_read.assert_called_once_with("scratch")
        assert [entry.path for entry in entries] == ["src/app.py", "README.md"]
