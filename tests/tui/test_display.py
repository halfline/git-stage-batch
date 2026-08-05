"""Tests for TUI display utilities."""

from io import StringIO
from unittest.mock import patch

from git_stage_batch.output.colors import Colors
from git_stage_batch.tui.display import print_status_bar
from git_stage_batch.tui.flow import FlowLocation, FlowState


class TestPrintStatusBar:
    """Tests for print_status_bar function."""

    def test_status_bar_with_colors(self):
        """Test status bar with colors enabled."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                stats = {"included": 5, "skipped": 2, "discarded": 1}
                flow_state = FlowState(source=FlowLocation.WORKING_TREE, target=FlowLocation.STAGING_AREA)
                print_status_bar(stats, flow_state)
                output = fake_out.getvalue()

        assert "Source:" in output
        assert "Target:" in output
        assert "Included:" in output
        assert " 5" in output
        assert "Skipped:" in output
        assert " 2" in output
        assert "Discarded:" in output
        assert " 1" in output
        assert "═" in output
        assert Colors.CYAN in output
        assert Colors.BOLD in output
        assert Colors.GRAY in output  # Arrow should be gray

    def test_colored_status_bar_translates_labels_and_arrow(self):
        """Color styling does not bypass translated flow/status text."""
        translations = {
            "Source:": "المصدر:",
            "Target:": "الهدف:",
            "Included: {count}": "المضمّن: {count}",
            "Skipped: {count}": "المتخطّى: {count}",
            "Discarded: {count}": "المُتجاهَل: {count}",
        }
        with (
            patch("sys.stdout", new=StringIO()) as fake_out,
            patch("sys.stdout.isatty", return_value=True),
            patch(
                "git_stage_batch.tui.display._",
                side_effect=lambda message: translations.get(message, message),
            ),
            patch(
                "git_stage_batch.tui.display.pgettext",
                return_value="←",
            ),
        ):
            print_status_bar(
                {"included": 5, "skipped": 2, "discarded": 1},
                FlowState(
                    source=FlowLocation.WORKING_TREE,
                    target=FlowLocation.STAGING_AREA,
                ),
            )

        output = fake_out.getvalue()
        assert "المصدر:" in output
        assert "الهدف:" in output
        assert "المضمّن: 5" in output
        assert "المتخطّى: 2" in output
        assert "المُتجاهَل: 1" in output
        assert f"{Colors.GRAY}←{Colors.RESET}" in output

    def test_status_bar_without_colors(self):
        """Test status bar without colors."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                stats = {"included": 5, "skipped": 2, "discarded": 1}
                flow_state = FlowState(source=FlowLocation.WORKING_TREE, target=FlowLocation.STAGING_AREA)
                print_status_bar(stats, flow_state)
                output = fake_out.getvalue()

        assert "Included: 5" in output
        assert "Skipped: 2" in output
        assert "Discarded: 1" in output
        assert Colors.CYAN not in output
        assert Colors.BOLD not in output

    def test_status_bar_with_zero_counts(self):
        """Test status bar with zero counts."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                stats = {"included": 0, "skipped": 0, "discarded": 0}
                flow_state = FlowState(source=FlowLocation.WORKING_TREE, target=FlowLocation.STAGING_AREA)
                print_status_bar(stats, flow_state)
                output = fake_out.getvalue()

        assert "Included: 0" in output
        assert "Skipped: 0" in output
        assert "Discarded: 0" in output

    def test_status_bar_missing_stats(self):
        """Test status bar with missing stats defaults to 0."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                stats = {}  # Empty dict
                flow_state = FlowState(source=FlowLocation.WORKING_TREE, target=FlowLocation.STAGING_AREA)
                print_status_bar(stats, flow_state)
                output = fake_out.getvalue()

        assert "Included: 0" in output
        assert "Skipped: 0" in output
        assert "Discarded: 0" in output
