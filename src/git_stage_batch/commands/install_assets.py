"""Install bundled assistant assets into the current repository."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
import subprocess

from ..data.asset_catalog import (
    ASSET_GROUPS as _ASSET_GROUPS,
    AssetGroup,
    CompanionAsset,
    Traversable,
)
from ..data.asset_installation import copy_asset_tree
from ..exceptions import CommandError
from ..i18n import _
from ..output.install_assets import (
    print_group_install_summary as _print_group_install_summary,
)
from ..utils.file_patterns import resolve_gitignore_style_patterns
from ..utils.git import get_git_repository_root_path, require_git_repository


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


def _get_companion_source(companion: CompanionAsset) -> Traversable:
    """Return the packaged source for a companion asset."""
    return resources.files("git_stage_batch").joinpath(*companion.source_segments)


def _get_entry_companion_assets(
    group: AssetGroup,
    entry_name: str,
) -> tuple[CompanionAsset, ...]:
    """Return companion assets required by one selected entry."""
    for companion_entry_name, companion_assets in group.entry_companion_assets:
        if companion_entry_name == entry_name:
            return companion_assets
    return ()


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


def command_install_assets(
    asset_group_name: str | None = None,
    filters: list[str] | None = None,
    *,
    force: bool = False,
) -> None:
    """Install bundled assets into the current repository."""
    require_git_repository()

    if asset_group_name is None:
        selected_groups = _ASSET_GROUPS
    else:
        try:
            selected_groups = {asset_group_name: _ASSET_GROUPS[asset_group_name]}
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
            for companion in _get_entry_companion_assets(group, entry_name):
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
        copy_asset_tree(entry, destination)

    for group, selected_entries in selected_entries_by_group:
        _print_group_install_summary(group, selected_entries.keys())
