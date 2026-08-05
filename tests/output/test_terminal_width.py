"""Tests for translated terminal display widths."""

from git_stage_batch.output.terminal_width import (
    pad_to_terminal_width,
    terminal_cell_width,
)


def test_terminal_width_handles_arabic_combining_and_wide_characters() -> None:
    assert terminal_cell_width("مرحبا") == 5
    assert terminal_cell_width("e\u0301") == 1
    assert terminal_cell_width("कि") == 1
    assert terminal_cell_width("界") == 2


def test_terminal_width_ignores_directional_isolates() -> None:
    assert terminal_cell_width("\u2068path\u2069") == 4
    assert pad_to_terminal_width("\u2068path\u2069", 6).endswith("  ")


def test_terminal_width_handles_common_emoji_clusters_without_buffering() -> None:
    assert terminal_cell_width("✈️") == 2
    assert terminal_cell_width("☕️") == 2
    assert terminal_cell_width("1️⃣") == 2
    assert terminal_cell_width("👩🏽‍💻") == 2
    assert terminal_cell_width("a‍b") == 2
