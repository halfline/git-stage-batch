"""Tests for packaged asset install planning."""

from __future__ import annotations

import pytest

from git_stage_batch.data.asset_install_plan import plan_asset_installs
from git_stage_batch.data.asset_selection import select_asset_entries
from git_stage_batch.exceptions import CommandError


def _relative_destinations(repo_root, planned_installs):
    """Return repo-relative destination strings for planned installs."""
    return sorted(
        str(planned_install.destination.relative_to(repo_root))
        for planned_install in planned_installs
    )


def test_plan_asset_installs_adds_group_companions(tmp_path):
    """Group companion assets should be planned with the selected entry."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries("codex-skills", ["commit-unstaged-changes"])

    planned = plan_asset_installs(selected, repo_root)

    assert _relative_destinations(repo_root, planned) == [
        ".agents/internal/commit-message-drafter.md",
        ".agents/skills/commit-unstaged-changes",
        ".codex/config.toml",
    ]


def test_plan_asset_installs_adds_entry_companions(tmp_path):
    """Entry-specific companion assets should be planned with the entry."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries(
        "claude-skills",
        ["decompose-and-commit-unstaged-changes"],
    )

    planned = plan_asset_installs(selected, repo_root)

    assert _relative_destinations(repo_root, planned) == [
        ".claude/agents/commit-message-drafter.md",
        ".claude/agents/decompose-analyzer.md",
        ".claude/agents/decompose-batch-peeler.md",
        ".claude/agents/decompose-deconstructor.md",
        ".claude/agents/decompose-rebuilder.md",
        ".claude/skills/decompose-and-commit-unstaged-changes",
    ]


def test_plan_asset_installs_rejects_existing_entry_without_force(tmp_path):
    """Existing entry destinations should require force mode."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    destination = repo_root / ".claude" / "skills" / "commit-unstaged-changes"
    destination.mkdir(parents=True)
    selected = select_asset_entries("claude-skills", ["commit-unstaged-changes"])

    with pytest.raises(
        CommandError,
        match="Refusing to overwrite existing claude skill 'commit-unstaged-changes'",
    ):
        plan_asset_installs(selected, repo_root)


def test_plan_asset_installs_rejects_existing_companion_without_force(tmp_path):
    """Existing companion destinations should require force mode."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    destination = repo_root / ".codex" / "config.toml"
    destination.parent.mkdir(parents=True)
    destination.write_text("local\n", encoding="utf-8")
    selected = select_asset_entries("codex-skills", ["commit-unstaged-changes"])

    with pytest.raises(
        CommandError,
        match=r"Refusing to overwrite existing codex config '\.codex/config.toml'",
    ):
        plan_asset_installs(selected, repo_root)


def test_plan_asset_installs_allows_existing_destination_with_force(tmp_path):
    """Force mode should allow existing destinations in the install plan."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    destination = repo_root / ".claude" / "skills" / "commit-unstaged-changes"
    destination.mkdir(parents=True)
    selected = select_asset_entries("claude-skills", ["commit-unstaged-changes"])

    planned = plan_asset_installs(selected, repo_root, force=True)

    assert ".claude/skills/commit-unstaged-changes" in _relative_destinations(
        repo_root,
        planned,
    )
