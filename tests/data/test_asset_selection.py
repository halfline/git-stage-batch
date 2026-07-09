"""Tests for asset group and entry selection."""

from __future__ import annotations

import pytest

from git_stage_batch.data.asset_catalog import ASSET_GROUPS
from git_stage_batch.data.asset_selection import select_asset_entries
from git_stage_batch.exceptions import CommandError


def test_select_asset_entries_returns_requested_group():
    """A named group should select entries from that asset group."""
    selected = select_asset_entries("claude-agents", None)

    assert len(selected) == 1
    assert selected[0].group is ASSET_GROUPS["claude-agents"]
    assert "commit-message-drafter" in selected[0].entries


def test_select_asset_entries_filters_across_groups():
    """Filters without a group should match entries in every asset group."""
    selected = select_asset_entries(None, ["commit-*"])

    assert [
        (group.group.display_name_plural, sorted(group.entries))
        for group in selected
    ] == [
        ("Claude agents", ["commit-message-drafter"]),
        ("Claude skills", ["commit-staged-changes", "commit-unstaged-changes"]),
        ("Codex skills", ["commit-staged-changes", "commit-unstaged-changes"]),
    ]


def test_select_asset_entries_rejects_unknown_group():
    """Unknown groups should raise the install-assets unknown-group error."""
    with pytest.raises(CommandError, match="Unknown asset group 'missing-group'"):
        select_asset_entries("missing-group", None)


def test_select_asset_entries_rejects_unmatched_group_filter():
    """Unmatched filters in one group should name that group."""
    with pytest.raises(
        CommandError,
        match="No bundled assets in 'claude-skills' matched: missing-skill",
    ):
        select_asset_entries("claude-skills", ["missing-skill"])


def test_select_asset_entries_rejects_unmatched_all_group_filter():
    """Unmatched filters across all groups should use the generic group name."""
    with pytest.raises(
        CommandError,
        match="No bundled assets in 'all asset groups' matched: missing-skill",
    ):
        select_asset_entries(None, ["missing-skill"])
