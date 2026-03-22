"""Tests for color codes."""

from git_stage_batch.output.colors import Colors


def test_colors_class_has_codes():
    """Test that Colors class has expected color codes."""
    assert hasattr(Colors, 'RESET')
    assert hasattr(Colors, 'BOLD')
    assert hasattr(Colors, 'RED')
    assert hasattr(Colors, 'GREEN')
    assert hasattr(Colors, 'CYAN')


def test_colors_enabled_returns_bool():
    """Test that Colors.enabled() returns a boolean."""
    result = Colors.enabled()
    assert isinstance(result, bool)
