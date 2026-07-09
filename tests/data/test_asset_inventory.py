"""Tests for packaged asset inventory lookup."""

from __future__ import annotations

import pytest

from git_stage_batch.data.asset_catalog import ASSET_GROUPS, AssetGroup
from git_stage_batch.data.asset_inventory import (
    get_companion_asset_source,
    get_entry_companion_assets,
    list_asset_group_entries,
)
from git_stage_batch.exceptions import CommandError


def test_list_asset_group_entries_names_agents_without_markdown_suffix():
    """Agent entries should use install names rather than packaged filenames."""
    entries = list_asset_group_entries(
        "claude-agents",
        ASSET_GROUPS["claude-agents"],
    )

    assert "commit-message-drafter" in entries
    assert "commit-message-drafter.md" not in entries
    assert entries["commit-message-drafter"].name == "commit-message-drafter.md"


def test_list_asset_group_entries_requires_marker_file():
    """Skill entries should exclude package support directories."""
    entries = list_asset_group_entries(
        "codex-skills",
        ASSET_GROUPS["codex-skills"],
    )

    assert sorted(entries) == [
        "commit-staged-changes",
        "commit-unstaged-changes",
        "decompose-and-commit-unstaged-changes",
    ]


def test_list_asset_group_entries_rejects_empty_group():
    """Missing packaged groups should raise the install-assets empty-group error."""
    group = AssetGroup(
        source_segments=("assets", "missing-group"),
        target_segments=(),
        display_name_singular="Missing asset",
        display_name_plural="Missing assets",
        required_entry="",
    )

    with pytest.raises(CommandError, match="No bundled assets are available"):
        list_asset_group_entries("missing-group", group)


def test_get_entry_companion_assets_returns_entry_specific_assets():
    """Entry companion lookup should return only the selected entry assets."""
    companions = get_entry_companion_assets(
        ASSET_GROUPS["claude-skills"],
        "decompose-and-commit-unstaged-changes",
    )

    assert [companion.source_segments[-1] for companion in companions] == [
        "decompose-analyzer.md",
        "decompose-batch-peeler.md",
        "decompose-deconstructor.md",
        "decompose-rebuilder.md",
    ]
    assert get_entry_companion_assets(ASSET_GROUPS["claude-skills"], "other") == ()


def test_get_companion_asset_source_returns_packaged_file():
    """Companion source lookup should resolve the packaged asset."""
    companion = ASSET_GROUPS["codex-skills"].companion_assets[1]
    source = get_companion_asset_source(companion)

    assert source.name == "config.toml"
    assert source.is_file()
