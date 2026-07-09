"""Packaged asset inventory lookup."""

from __future__ import annotations

from importlib import resources

from .asset_catalog import AssetGroup, CompanionAsset, Traversable
from ..exceptions import CommandError
from ..i18n import _


def _get_asset_group_root(group: AssetGroup) -> Traversable:
    """Return the packaged root for an asset group."""
    return resources.files("git_stage_batch").joinpath(*group.source_segments)


def _get_install_entry_name(group: AssetGroup, entry: Traversable) -> str:
    """Return the user-facing install name for one bundled asset entry."""
    if group.required_entry == "" and entry.name.endswith(".md"):
        return entry.name[:-3]
    return entry.name


def list_asset_group_entries(
    group_name: str,
    group: AssetGroup,
) -> dict[str, Traversable]:
    """List installable asset entries for a group."""
    try:
        root = _get_asset_group_root(group)
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


def get_companion_asset_source(companion: CompanionAsset) -> Traversable:
    """Return the packaged source for a companion asset."""
    return resources.files("git_stage_batch").joinpath(*companion.source_segments)


def get_entry_companion_assets(
    group: AssetGroup,
    entry_name: str,
) -> tuple[CompanionAsset, ...]:
    """Return companion assets required by one selected entry."""
    for companion_entry_name, companion_assets in group.entry_companion_assets:
        if companion_entry_name == entry_name:
            return companion_assets
    return ()
