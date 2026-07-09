"""Install bundled assistant assets into the current repository."""

from __future__ import annotations

from pathlib import Path
import subprocess

from ..data.asset_catalog import (
    ASSET_GROUPS as _ASSET_GROUPS,
    AssetGroup,
    Traversable,
)
from ..data.asset_installation import (
    copy_asset_tree,
    validate_asset_destination_path as _validate_asset_destination,
)
from ..data.asset_inventory import (
    get_companion_asset_source as _companion_asset_source,
    get_entry_companion_assets as _entry_companion_assets,
    list_asset_group_entries as _asset_group_entries,
)
from ..exceptions import CommandError
from ..i18n import _
from ..output.install_assets import (
    print_group_install_summary as _print_group_install_summary,
)
from ..utils.file_patterns import resolve_gitignore_style_patterns
from ..utils.git import get_git_repository_root_path, require_git_repository


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
        available_entries = _asset_group_entries(group_name, group)
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
            _validate_asset_destination(entry, destination, repo_root)
            if destination.exists() and not force:
                raise CommandError(
                    _("Refusing to overwrite existing {kind} '{name}'. Use --force to replace it.").format(
                        kind=group.display_name_singular.lower(),
                        name=entry_name,
                    )
                )
            planned_installs.append((entry, destination))
            for companion in _entry_companion_assets(group, entry_name):
                destination = repo_root.joinpath(*companion.target_segments)
                companion_source = _companion_asset_source(companion)
                _validate_asset_destination(companion_source, destination, repo_root)
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
            companion_source = _companion_asset_source(companion)
            _validate_asset_destination(companion_source, destination, repo_root)
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
