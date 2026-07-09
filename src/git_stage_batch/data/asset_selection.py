"""Asset group and entry selection."""

from __future__ import annotations

from dataclasses import dataclass
import subprocess

from .asset_catalog import ASSET_GROUPS, AssetGroup, Traversable
from .asset_inventory import list_asset_group_entries
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_patterns import resolve_gitignore_style_patterns


@dataclass(frozen=True)
class SelectedAssetGroup:
    """Selected installable entries for one asset group."""

    group: AssetGroup
    entries: dict[str, Traversable]


def _select_asset_groups(asset_group_name: str | None) -> dict[str, AssetGroup]:
    """Return asset groups selected by the optional group name."""
    if asset_group_name is None:
        return ASSET_GROUPS
    try:
        return {asset_group_name: ASSET_GROUPS[asset_group_name]}
    except KeyError as error:
        raise CommandError(
            _("Unknown asset group '{group}'.").format(group=asset_group_name)
        ) from error


def select_asset_entries(
    asset_group_name: str | None,
    filters: list[str] | None,
) -> tuple[SelectedAssetGroup, ...]:
    """Return selected installable entries for asset groups."""
    selected_entries_by_group: list[SelectedAssetGroup] = []
    for group_name, group in _select_asset_groups(asset_group_name).items():
        available_entries = list_asset_group_entries(group_name, group)
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
        selected_entries_by_group.append(
            SelectedAssetGroup(group=group, entries=selected_entries)
        )

    if not selected_entries_by_group:
        group_text = asset_group_name if asset_group_name is not None else _("all asset groups")
        raise CommandError(
            _("No bundled assets in '{group}' matched: {filters}.").format(
                group=group_text,
                filters=", ".join(filters or []),
            )
        )

    return tuple(selected_entries_by_group)
