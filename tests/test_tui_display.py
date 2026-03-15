"""Tests for TUI display utilities."""

from io import StringIO
from unittest.mock import patch

from git_stage_batch.display import Colors
from git_stage_batch.tui_display import print_action_summary, print_status_bar


class TestPrintStatusBar:
    """Tests for print_status_bar function."""

    def test_status_bar_with_colors(self):
        """Test status bar with colors enabled."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                stats = {"included": 5, "skipped": 2, "discarded": 1}
                print_status_bar(stats)
                output = fake_out.getvalue()

        assert "Included: 5" in output
        assert "Skipped: 2" in output
        assert "Discarded: 1" in output
        assert "═" in output
        assert Colors.CYAN in output
        assert Colors.BOLD in output

    def test_status_bar_without_colors(self):
        """Test status bar without colors."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                stats = {"included": 5, "skipped": 2, "discarded": 1}
                print_status_bar(stats)
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
                print_status_bar(stats)
                output = fake_out.getvalue()

        assert "Included: 0" in output
        assert "Skipped: 0" in output
        assert "Discarded: 0" in output

    def test_status_bar_missing_stats(self):
        """Test status bar with missing stats defaults to 0."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                stats = {}  # Empty dict
                print_status_bar(stats)
                output = fake_out.getvalue()

        assert "Included: 0" in output
        assert "Skipped: 0" in output
        assert "Discarded: 0" in output


class TestPrintActionSummary:
    """Tests for print_action_summary function."""

    def test_staged_action_with_color(self):
        """Test staged action uses green color."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                print_action_summary("Staged hunk")
                output = fake_out.getvalue()

        assert "✓ Staged hunk" in output
        assert Colors.GREEN in output
        assert Colors.RESET in output

    def test_skipped_action_with_color(self):
        """Test skipped action uses cyan color."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                print_action_summary("Skipped hunk")
                output = fake_out.getvalue()

        assert "✓ Skipped hunk" in output
        assert Colors.CYAN in output

    def test_discarded_action_with_color(self):
        """Test discarded action uses red color."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                print_action_summary("Discarded hunk")
                output = fake_out.getvalue()

        assert "✓ Discarded hunk" in output
        assert Colors.RED in output

    def test_action_without_color(self):
        """Test action summary without colors."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                print_action_summary("Staged hunk")
                output = fake_out.getvalue()

        assert "✓ Staged hunk" in output
        assert Colors.GREEN not in output

    def test_action_with_details(self):
        """Test action summary with details."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=False):
                print_action_summary("Skipped file", "(3 hunks)")
                output = fake_out.getvalue()

        assert "✓ Skipped file (3 hunks)" in output

    def test_included_action_uses_green(self):
        """Test that 'included' keyword triggers green color."""
        with patch("sys.stdout", new=StringIO()) as fake_out:
            with patch("sys.stdout.isatty", return_value=True):
                print_action_summary("Included from batch")
                output = fake_out.getvalue()

        assert Colors.GREEN in output
