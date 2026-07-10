"""Tests for install-assets output rendering."""

from __future__ import annotations

from git_stage_batch.data.asset_catalog import AssetGroup
from git_stage_batch.output.install_assets import print_group_install_summary


def _asset_group() -> AssetGroup:
    """Return a minimal asset group for rendering tests."""
    return AssetGroup(
        source_segments=(),
        target_segments=(),
        display_name_singular="Example asset",
        display_name_plural="Example assets",
        required_entry="",
    )


def test_print_group_install_summary_renders_single_entry(capsys):
    """Single-entry installs should use the singular asset label."""
    print_group_install_summary(_asset_group(), ("commit-message-drafter",))

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "Installed Example asset 'commit-message-drafter'" in captured.err


def test_print_group_install_summary_renders_multiple_entries(capsys):
    """Multi-entry installs should use the plural asset label."""
    print_group_install_summary(_asset_group(), ("alpha", "beta"))

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "Installed Example assets: alpha, beta" in captured.err
