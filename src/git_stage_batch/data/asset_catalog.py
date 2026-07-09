"""Bundled installable asset catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Traversable(Protocol):
    """Subset of importlib.resources Traversable used by asset installation."""

    name: str

    def is_dir(self) -> bool: ...
    def is_file(self) -> bool: ...
    def iterdir(self): ...
    def joinpath(self, *pathsegments: str): ...
    def read_bytes(self) -> bytes: ...


@dataclass(frozen=True)
class AssetGroup:
    """Configuration for an installable asset group."""

    source_segments: tuple[str, ...]
    target_segments: tuple[str, ...]
    display_name_singular: str
    display_name_plural: str
    required_entry: str
    companion_assets: tuple["CompanionAsset", ...] = ()
    entry_companion_assets: tuple[tuple[str, tuple["CompanionAsset", ...]], ...] = ()


@dataclass(frozen=True)
class CompanionAsset:
    """Additional packaged asset installed alongside a selected group."""

    source_segments: tuple[str, ...]
    target_segments: tuple[str, ...]
    display_name: str


ASSET_GROUPS: dict[str, AssetGroup] = {
    "claude-agents": AssetGroup(
        source_segments=("assets", "claude-agents"),
        target_segments=(".claude", "agents"),
        display_name_singular="Claude agent",
        display_name_plural="Claude agents",
        required_entry="",
    ),
    "claude-skills": AssetGroup(
        source_segments=("assets", "claude-skills"),
        target_segments=(".claude", "skills"),
        display_name_singular="Claude skill",
        display_name_plural="Claude skills",
        required_entry="SKILL.md",
        companion_assets=(
            CompanionAsset(
                source_segments=("assets", "claude-agents", "commit-message-drafter.md"),
                target_segments=(".claude", "agents", "commit-message-drafter.md"),
                display_name="Claude agent",
            ),
        ),
        entry_companion_assets=(
            (
                "decompose-and-commit-unstaged-changes",
                (
                    CompanionAsset(
                        source_segments=("assets", "claude-agents", "decompose-analyzer.md"),
                        target_segments=(".claude", "agents", "decompose-analyzer.md"),
                        display_name="Claude agent",
                    ),
                    CompanionAsset(
                        source_segments=("assets", "claude-agents", "decompose-batch-peeler.md"),
                        target_segments=(".claude", "agents", "decompose-batch-peeler.md"),
                        display_name="Claude agent",
                    ),
                    CompanionAsset(
                        source_segments=("assets", "claude-agents", "decompose-deconstructor.md"),
                        target_segments=(".claude", "agents", "decompose-deconstructor.md"),
                        display_name="Claude agent",
                    ),
                    CompanionAsset(
                        source_segments=("assets", "claude-agents", "decompose-rebuilder.md"),
                        target_segments=(".claude", "agents", "decompose-rebuilder.md"),
                        display_name="Claude agent",
                    ),
                ),
            ),
        ),
    ),
    "codex-skills": AssetGroup(
        source_segments=("assets", "codex-skills"),
        target_segments=(".agents", "skills"),
        display_name_singular="Codex skill",
        display_name_plural="Codex skills",
        required_entry="SKILL.md",
        companion_assets=(
            CompanionAsset(
                source_segments=(
                    "assets",
                    "codex-skills",
                    "internal",
                    "commit-message-drafter.md",
                ),
                target_segments=(".agents", "internal", "commit-message-drafter.md"),
                display_name="Codex internal asset",
            ),
            CompanionAsset(
                source_segments=("assets", "codex-skills", "config", "config.toml"),
                target_segments=(".codex", "config.toml"),
                display_name="Codex config",
            ),
        ),
    ),
}
