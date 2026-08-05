"""Bundled installable asset catalog."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from ..i18n import _, ngettext, npgettext, pgettext


class Traversable(Protocol):
    """Resource path operations required by asset installation."""

    @property
    def name(self) -> str: ...

    def is_dir(self) -> bool: ...
    def is_file(self) -> bool: ...
    def iterdir(self) -> Iterator[Traversable]: ...
    def joinpath(self, child: str) -> Traversable: ...
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


def asset_group_display_name(group: AssetGroup, count: int) -> str:
    """Return one asset group's localized name for ``count`` entries."""
    names = (group.display_name_singular, group.display_name_plural)
    if names == ("Claude agent", "Claude agents"):
        return npgettext(
            "asset group kind",
            "Claude agent",
            "Claude agents",
            count,
        )
    if names == ("Claude skill", "Claude skills"):
        return npgettext(
            "asset group kind",
            "Claude skill",
            "Claude skills",
            count,
        )
    if names == ("Codex skill", "Codex skills"):
        return npgettext(
            "asset group kind",
            "Codex skill",
            "Codex skills",
            count,
        )
    return ngettext(*names, count)


def asset_group_menu_label(group: AssetGroup) -> str:
    """Return one asset group's generic plural menu label."""
    plural_name = group.display_name_plural
    if plural_name == "Claude agents":
        return pgettext("asset group menu label", "Claude agents")
    if plural_name == "Claude skills":
        return pgettext("asset group menu label", "Claude skills")
    if plural_name == "Codex skills":
        return pgettext("asset group menu label", "Codex skills")
    return plural_name


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
                source_segments=(
                    "assets",
                    "claude-agents",
                    "commit-message-drafter.md",
                ),
                target_segments=(".claude", "agents", "commit-message-drafter.md"),
                display_name=_("Claude agent"),
            ),
        ),
        entry_companion_assets=(
            (
                "publish-unpushed-commits",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-history",
                        ),
                        target_segments=(".claude", "skills", "refine-history"),
                        display_name=_("Claude skill"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".claude",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Claude skill"),
                    ),
                ),
            ),
            (
                "decompose-and-commit-unstaged-changes",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-agents",
                            "decompose-analyzer.md",
                        ),
                        target_segments=(".claude", "agents", "decompose-analyzer.md"),
                        display_name=_("Claude agent"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-agents",
                            "decompose-batch-peeler.md",
                        ),
                        target_segments=(
                            ".claude",
                            "agents",
                            "decompose-batch-peeler.md",
                        ),
                        display_name=_("Claude agent"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-agents",
                            "decompose-deconstructor.md",
                        ),
                        target_segments=(
                            ".claude",
                            "agents",
                            "decompose-deconstructor.md",
                        ),
                        display_name=_("Claude agent"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-agents",
                            "decompose-rebuilder.md",
                        ),
                        target_segments=(".claude", "agents", "decompose-rebuilder.md"),
                        display_name=_("Claude agent"),
                    ),
                    CompanionAsset(
                        source_segments=("assets", "claude-skills", "refine-history"),
                        target_segments=(".claude", "skills", "refine-history"),
                        display_name=_("Claude skill"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".claude",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Claude skill"),
                    ),
                ),
            ),
            (
                "refine-history",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "claude-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".claude",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Claude skill"),
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
                display_name=_("Codex internal asset"),
            ),
            CompanionAsset(
                source_segments=("assets", "codex-skills", "config", "config.toml"),
                target_segments=(".codex", "config.toml"),
                display_name=_("Codex config"),
            ),
        ),
        entry_companion_assets=(
            (
                "publish-unpushed-commits",
                (
                    CompanionAsset(
                        source_segments=("assets", "codex-skills", "refine-history"),
                        target_segments=(".agents", "skills", "refine-history"),
                        display_name=_("Codex skill"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "codex-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".agents",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Codex skill"),
                    ),
                ),
            ),
            (
                "decompose-and-commit-unstaged-changes",
                (
                    CompanionAsset(
                        source_segments=("assets", "codex-skills", "refine-history"),
                        target_segments=(".agents", "skills", "refine-history"),
                        display_name=_("Codex skill"),
                    ),
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "codex-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".agents",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Codex skill"),
                    ),
                ),
            ),
            (
                "refine-history",
                (
                    CompanionAsset(
                        source_segments=(
                            "assets",
                            "codex-skills",
                            "refine-commit-messages",
                        ),
                        target_segments=(
                            ".agents",
                            "skills",
                            "refine-commit-messages",
                        ),
                        display_name=_("Codex skill"),
                    ),
                ),
            ),
        ),
    ),
}
