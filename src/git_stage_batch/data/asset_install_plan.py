"""Packaged asset install planning."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .asset_catalog import Traversable, asset_group_display_name
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
                kind=display_kind,
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
    planned_destinations: dict[Path, tuple[str, ...]] = {}

    def append_install(
        source: Traversable,
        destination: Path,
        *,
        source_key: tuple[str, ...],
        display_kind: str,
        display_name: str,
    ) -> None:
        # A selected entry can also be a dependency of another selected entry.
        # Install that shared tree once while retaining both selections in the
        # user-facing summary.
        if destination in planned_destinations:
            if planned_destinations[destination] != source_key:
                raise CommandError(
                    _(
                        "Bundled assets from different sources target the same "
                        "destination: '{destination}'."
                    ).format(destination=destination.relative_to(repo_root))
                )
            return
        _validate_overwrite(
            source,
            destination,
            repo_root,
            force=force,
            display_kind=display_kind,
            display_name=display_name,
        )
        planned_installs.append(
            PlannedAssetInstall(source=source, destination=destination)
        )
        planned_destinations[destination] = source_key

    for selected_group in selected_entries_by_group:
        group = selected_group.group
        selected_entries = selected_group.entries
        target_root = repo_root.joinpath(*group.target_segments)
        for entry_name, entry in selected_entries.items():
            destination = target_root / (entry.name if entry.is_file() else entry_name)
            append_install(
                entry,
                destination,
                source_key=(*group.source_segments, entry.name),
                display_kind=asset_group_display_name(group, 1),
                display_name=entry_name,
            )
            for companion in get_entry_companion_assets(group, entry_name):
                destination = repo_root.joinpath(*companion.target_segments)
                companion_source = get_companion_asset_source(companion)
                append_install(
                    companion_source,
                    destination,
                    source_key=companion.source_segments,
                    display_kind=companion.display_name,
                    display_name=str(destination.relative_to(repo_root)),
                )
        for companion in group.companion_assets:
            destination = repo_root.joinpath(*companion.target_segments)
            companion_source = get_companion_asset_source(companion)
            append_install(
                companion_source,
                destination,
                source_key=companion.source_segments,
                display_kind=companion.display_name,
                display_name=str(destination.relative_to(repo_root)),
            )

    return tuple(planned_installs)
