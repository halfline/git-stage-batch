"""Tests for color codes."""

from git_stage_batch.output import colors
from git_stage_batch.output.colors import Colors


def test_colors_class_has_codes():
    """Test that Colors class has expected color codes."""
    assert hasattr(Colors, 'RESET')
    assert hasattr(Colors, 'BOLD')
    assert hasattr(Colors, 'RED')
    assert hasattr(Colors, 'GREEN')
    assert hasattr(Colors, 'YELLOW')
    assert hasattr(Colors, 'CYAN')


def test_colors_enabled_returns_bool():
    """Test that Colors.enabled() returns a boolean."""
    result = Colors.enabled()
    assert isinstance(result, bool)


def test_format_hotkey_isolates_the_key_in_rtl_output(monkeypatch):
    monkeypatch.setattr(
        colors,
        "bidi_isolate",
        lambda value: f"<isolate>{value}</isolate>",
    )

    assert colors.format_hotkey("تضمين", "i") == ("<isolate>[i]</isolate> تضمين")


def test_format_hotkey_places_uppercase_key_at_exact_match(monkeypatch):
    monkeypatch.setattr(colors.Colors, "enabled", staticmethod(lambda: False))

    assert colors.format_hotkey("status S", "S") == "status [S]"


def test_format_hotkey_handles_expanding_unicode_casefold(monkeypatch):
    monkeypatch.setattr(colors.Colors, "enabled", staticmethod(lambda: False))

    assert colors.format_hotkey("İi", "i") == "İ[i]"
