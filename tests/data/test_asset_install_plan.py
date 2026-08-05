"""Tests for packaged asset install planning."""

from __future__ import annotations

import pytest

from git_stage_batch.data.asset_catalog import AssetGroup
from git_stage_batch.data.asset_install_plan import plan_asset_installs
from git_stage_batch.data.asset_selection import (
    SelectedAssetGroup,
    select_asset_entries,
)
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
        ".claude/skills/refine-commit-messages",
        ".claude/skills/refine-history",
    ]


def test_plan_refine_history_as_standalone_skill(tmp_path):
    """History refinement should install without decomposition agents."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries("claude-skills", ["refine-history"])

    planned = plan_asset_installs(selected, repo_root)

    assert _relative_destinations(repo_root, planned) == [
        ".claude/agents/commit-message-drafter.md",
        ".claude/skills/refine-commit-messages",
        ".claude/skills/refine-history",
    ]


def test_plan_refine_commit_messages_as_standalone_skill(tmp_path):
    """Message refinement should install without full history refinement."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries(
        "codex-skills",
        ["refine-commit-messages"],
    )

    planned = plan_asset_installs(selected, repo_root)

    assert _relative_destinations(repo_root, planned) == [
        ".agents/internal/commit-message-drafter.md",
        ".agents/skills/refine-commit-messages",
        ".codex/config.toml",
    ]


@pytest.mark.parametrize(
    ("group_name", "skill_root"),
    (
        ("claude-skills", ".claude/skills"),
        ("codex-skills", ".agents/skills"),
    ),
)
def test_plan_publisher_adds_refinement_dependencies(
    tmp_path,
    group_name,
    skill_root,
):
    """Publishing should install both history-refinement dependencies."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries(group_name, ["publish-unpushed-commits"])

    destinations = _relative_destinations(
        repo_root,
        plan_asset_installs(selected, repo_root),
    )

    assert f"{skill_root}/publish-unpushed-commits" in destinations
    assert f"{skill_root}/refine-history" in destinations
    assert f"{skill_root}/refine-commit-messages" in destinations


def test_plan_deduplicates_selected_dependency(tmp_path):
    """Selecting all skills should plan a shared dependency only once."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    selected = select_asset_entries("claude-skills", None)

    destinations = _relative_destinations(
        repo_root,
        plan_asset_installs(selected, repo_root),
    )

    assert destinations.count(".claude/skills/refine-history") == 1
    assert destinations.count(".claude/skills/refine-commit-messages") == 1


def test_plan_rejects_different_sources_for_one_destination(tmp_path):
    """Destination deduplication must not silently choose one source."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    first_source = tmp_path / "first" / "skill"
    second_source = tmp_path / "second" / "skill"
    first_source.mkdir(parents=True)
    second_source.mkdir(parents=True)
    first_group = AssetGroup(
        source_segments=("first",),
        target_segments=(".skills",),
        display_name_singular="Skill",
        display_name_plural="Skills",
        required_entry="SKILL.md",
    )
    second_group = AssetGroup(
        source_segments=("second",),
        target_segments=(".skills",),
        display_name_singular="Skill",
        display_name_plural="Skills",
        required_entry="SKILL.md",
    )
    selected = (
        SelectedAssetGroup(first_group, {"skill": first_source}),
        SelectedAssetGroup(second_group, {"skill": second_source}),
    )

    with pytest.raises(
        CommandError,
        match=r"different sources target the same destination: '\.skills/skill'",
    ):
        plan_asset_installs(selected, repo_root)


def test_plan_decompose_rejects_existing_refine_dependency(tmp_path):
    """A decompose install should not overwrite an existing refine skill."""
    repo_root = tmp_path / "repo"
    dependency = repo_root / ".claude" / "skills" / "refine-history"
    dependency.mkdir(parents=True)
    selected = select_asset_entries(
        "claude-skills",
        ["decompose-and-commit-unstaged-changes"],
    )

    with pytest.raises(
        CommandError,
        match=r"Refusing to overwrite existing Claude skill '\.claude/skills/refine-history'",
    ):
        plan_asset_installs(selected, repo_root)


def test_plan_asset_installs_rejects_existing_entry_without_force(tmp_path):
    """Existing entry destinations should require force mode."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    destination = repo_root / ".claude" / "skills" / "commit-unstaged-changes"
    destination.mkdir(parents=True)
    selected = select_asset_entries("claude-skills", ["commit-unstaged-changes"])

    with pytest.raises(
        CommandError,
        match="Refusing to overwrite existing Claude skill 'commit-unstaged-changes'",
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
        match=r"Refusing to overwrite existing Codex config '\.codex/config.toml'",
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
