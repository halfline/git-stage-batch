"""Install bundled assistant assets into the current repository."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
import subprocess
from typing import Protocol
import sys

from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns
from ..utils.file_io import write_file_bytes
from ..utils.git import get_git_repository_root_path, require_git_repository


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
    ),
    "codex-skills": AssetGroup(
        source_segments=("assets", "codex-skills"),
        target_segments=(".agents", "skills"),
        display_name_singular="Codex skill",
        display_name_plural="Codex skills",
        required_entry="SKILL.md",
        companion_assets=(
            CompanionAsset(
                source_segments=("assets", "codex-skills", "internal", "commit-message-drafter.md"),
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


def _get_group_root(group: AssetGroup) -> Traversable:
    """Return the packaged root for an asset group."""
    return resources.files("git_stage_batch").joinpath(*group.source_segments)


def _get_install_entry_name(group: AssetGroup, entry: Traversable) -> str:
    """Return the user-facing install name for one bundled asset entry."""
    if group.required_entry == "" and entry.name.endswith(".md"):
        return entry.name[:-3]
    return entry.name


def _list_group_entries(group_name: str, group: AssetGroup) -> dict[str, Traversable]:
    """List installable asset entries for a group."""
    try:
        root = _get_group_root(group)
        entries = {
            _get_install_entry_name(group, entry): entry
            for entry in root.iterdir()
            if (
                entry.is_file()
                if group.required_entry == ""
                else entry.is_dir() and entry.joinpath(group.required_entry).is_file()
            )
        }
    except (FileNotFoundError, ModuleNotFoundError):
        entries = {}

    if not entries:
        raise CommandError(
            _("No bundled assets are available for '{group}'.").format(group=group_name)
        )
    return dict(sorted(entries.items()))


def _copy_traversable_tree(source: Traversable, destination: Path) -> None:
    """Copy a Traversable tree into a filesystem path."""
    if source.is_dir():
        for child in source.iterdir():
            _copy_traversable_tree(child, destination / child.name)
        return
    if source.is_file():
        write_file_bytes(destination, source.read_bytes())


def _get_companion_source(companion: CompanionAsset) -> Traversable:
    """Return the packaged source for a companion asset."""
    return resources.files("git_stage_batch").joinpath(*companion.source_segments)


def _validate_destination_path_shape(
    source: Traversable,
    destination: Path,
    repo_root: Path,
) -> None:
    """Reject installs that would collide with non-directory path components."""
    for parent in destination.parents:
        if parent == repo_root.parent:
            break
        if not parent.exists():
            continue
        if not parent.is_dir():
            raise CommandError(
                _("Cannot install bundled assets because '{path}' is not a directory.").format(
                    path=str(parent.relative_to(repo_root)),
                )
            )

    if not destination.exists():
        return

    if source.is_dir() and not destination.is_dir():
        raise CommandError(
            _("Cannot install bundled assets because '{path}' is not a directory.").format(
                path=str(destination.relative_to(repo_root)),
            )
        )
    if source.is_file() and destination.is_dir():
        raise CommandError(
            _("Cannot install bundled assets because '{path}' is a directory.").format(
                path=str(destination.relative_to(repo_root)),
            )
        )


def _print_group_install_summary(
    group: AssetGroup,
    selected_entries: dict[str, Traversable],
) -> None:
    """Print a summary of what was installed for one asset group."""
    installed_names = ", ".join(selected_entries)
    if len(selected_entries) == 1:
        print(
            _("✓ Installed {kind} '{name}'").format(
                kind=group.display_name_singular,
                name=next(iter(selected_entries)),
            ),
            file=sys.stderr,
        )
        return
    print(
        _("✓ Installed {kind}: {names}").format(
            kind=group.display_name_plural,
            names=installed_names,
        ),
        file=sys.stderr,
    )


def command_install_assets(
    asset_group_name: str | None = None,
    filters: list[str] | None = None,
    *,
    force: bool = False,
) -> None:
    """Install bundled assets into the current repository."""
    require_git_repository()

    if asset_group_name is None:
        selected_groups = ASSET_GROUPS
    else:
        try:
            selected_groups = {asset_group_name: ASSET_GROUPS[asset_group_name]}
        except KeyError as error:
            raise CommandError(
                _("Unknown asset group '{group}'.").format(group=asset_group_name)
            ) from error

    selected_entries_by_group: list[tuple[AssetGroup, dict[str, Traversable]]] = []
    for group_name, group in selected_groups.items():
        available_entries = _list_group_entries(group_name, group)
        if filters is None:
            selected_entries = available_entries
        else:
            try:
                selected_names = resolve_gitignore_style_patterns(available_entries, filters)
            except subprocess.CalledProcessError:
                selected_names = []
            if not selected_names:
                continue
            selected_entries = {
                entry_name: available_entries[entry_name]
                for entry_name in selected_names
            }
        selected_entries_by_group.append((group, selected_entries))

    if not selected_entries_by_group:
        group_text = asset_group_name if asset_group_name is not None else _("all asset groups")
        raise CommandError(
            _("No bundled assets in '{group}' matched: {filters}.").format(
                group=group_text,
                filters=", ".join(filters or []),
            )
        )

    repo_root = get_git_repository_root_path()
    planned_installs: list[tuple[Traversable, Path]] = []
    for group, selected_entries in selected_entries_by_group:
        target_root = repo_root.joinpath(*group.target_segments)
        for entry_name, entry in selected_entries.items():
            destination = target_root / (entry.name if entry.is_file() else entry_name)
            _validate_destination_path_shape(entry, destination, repo_root)
            if destination.exists() and not force:
                raise CommandError(
                    _("Refusing to overwrite existing {kind} '{name}'. Use --force to replace it.").format(
                        kind=group.display_name_singular.lower(),
                        name=entry_name,
                    )
                )
            planned_installs.append((entry, destination))
        for companion in group.companion_assets:
            destination = repo_root.joinpath(*companion.target_segments)
            companion_source = _get_companion_source(companion)
            _validate_destination_path_shape(companion_source, destination, repo_root)
            if destination.exists() and not force:
                raise CommandError(
                    _("Refusing to overwrite existing {kind} '{name}'. Use --force to replace it.").format(
                        kind=companion.display_name.lower(),
                        name=str(destination.relative_to(repo_root)),
                    )
                )
            planned_installs.append((companion_source, destination))

    for entry, destination in planned_installs:
        _copy_traversable_tree(entry, destination)

    for group, selected_entries in selected_entries_by_group:
        _print_group_install_summary(group, selected_entries)
