"""Install bundled assistant assets into the current repository."""

from __future__ import annotations

from pathlib import Path

from ..data.asset_catalog import Traversable
from ..data.asset_installation import (
    copy_asset_tree,
    validate_asset_destination_path as _validate_asset_destination,
)
from ..data.asset_inventory import (
    get_companion_asset_source as _companion_asset_source,
    get_entry_companion_assets as _entry_companion_assets,
)
from ..data.asset_selection import select_asset_entries as _select_asset_entries
from ..exceptions import CommandError
from ..i18n import _
from ..output.install_assets import (
    print_group_install_summary as _print_group_install_summary,
)
from ..utils.git import get_git_repository_root_path, require_git_repository


def command_install_assets(
    asset_group_name: str | None = None,
    filters: list[str] | None = None,
    *,
    force: bool = False,
) -> None:
    """Install bundled assets into the current repository."""
    require_git_repository()

    selected_entries_by_group = _select_asset_entries(asset_group_name, filters)

    repo_root = get_git_repository_root_path()
    planned_installs: list[tuple[Traversable, Path]] = []
    for selected_group in selected_entries_by_group:
        group = selected_group.group
        selected_entries = selected_group.entries
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

    for selected_group in selected_entries_by_group:
        _print_group_install_summary(
            selected_group.group,
            selected_group.entries.keys(),
        )
