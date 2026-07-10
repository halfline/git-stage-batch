"""Packaged asset install planning."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .asset_catalog import Traversable
from .asset_installation import validate_asset_destination_path
from .asset_inventory import (
    get_companion_asset_source,
    get_entry_companion_assets,
)
from .asset_selection import SelectedAssetGroup
from ..exceptions import CommandError
from ..i18n import _


@dataclass(frozen=True)
class PlannedAssetInstall:
    """One packaged asset source and destination."""

    source: Traversable
    destination: Path


def _validate_overwrite(
    source: Traversable,
    destination: Path,
    repo_root: Path,
    *,
    force: bool,
    display_kind: str,
    display_name: str,
) -> None:
    """Reject an existing destination unless force mode is active."""
    validate_asset_destination_path(source, destination, repo_root)
    if destination.exists() and not force:
        raise CommandError(
            _("Refusing to overwrite existing {kind} '{name}'. Use --force to replace it.").format(
                kind=display_kind.lower(),
                name=display_name,
            )
        )


def plan_asset_installs(
    selected_entries_by_group: Iterable[SelectedAssetGroup],
    repo_root: Path,
    *,
    force: bool = False,
) -> tuple[PlannedAssetInstall, ...]:
    """Return the packaged asset sources and destinations to install."""
    planned_installs: list[PlannedAssetInstall] = []

    for selected_group in selected_entries_by_group:
        group = selected_group.group
        selected_entries = selected_group.entries
        target_root = repo_root.joinpath(*group.target_segments)
        for entry_name, entry in selected_entries.items():
            destination = target_root / (entry.name if entry.is_file() else entry_name)
            _validate_overwrite(
                entry,
                destination,
                repo_root,
                force=force,
                display_kind=group.display_name_singular,
                display_name=entry_name,
            )
            planned_installs.append(
                PlannedAssetInstall(source=entry, destination=destination)
            )
            for companion in get_entry_companion_assets(group, entry_name):
                destination = repo_root.joinpath(*companion.target_segments)
                companion_source = get_companion_asset_source(companion)
                _validate_overwrite(
                    companion_source,
                    destination,
                    repo_root,
                    force=force,
                    display_kind=companion.display_name,
                    display_name=str(destination.relative_to(repo_root)),
                )
                planned_installs.append(
                    PlannedAssetInstall(
                        source=companion_source,
                        destination=destination,
                    )
                )
        for companion in group.companion_assets:
            destination = repo_root.joinpath(*companion.target_segments)
            companion_source = get_companion_asset_source(companion)
            _validate_overwrite(
                companion_source,
                destination,
                repo_root,
                force=force,
                display_kind=companion.display_name,
                display_name=str(destination.relative_to(repo_root)),
            )
            planned_installs.append(
                PlannedAssetInstall(source=companion_source, destination=destination)
            )

    return tuple(planned_installs)
