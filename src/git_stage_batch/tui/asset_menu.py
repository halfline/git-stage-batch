"""Bundled-asset installation menu for interactive mode."""

from __future__ import annotations

import shlex
import sys

from ..commands.install_assets import ASSET_GROUPS, command_install_assets
from ..i18n import _


def handle_asset_menu() -> None:
    """Prompt for assistant asset install options and run the installer."""
    group_names = list(ASSET_GROUPS)

    print()
    print(_("Install bundled assistant assets:"))
    print(f"  [1] {_('all asset groups')}")
    for idx, group_name in enumerate(group_names, 2):
        print(f"  [{idx}] {group_name}")

    try:
        choice = input(_("Group (empty to cancel): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not choice:
        return

    asset_group_name: str | None
    if choice == "1" or choice.lower() in ("all", _("all asset groups")):
        asset_group_name = None
    elif choice.isdigit():
        group_idx = int(choice) - 2
        if 0 <= group_idx < len(group_names):
            asset_group_name = group_names[group_idx]
        else:
            print(_("\nInvalid selection."), file=sys.stderr)
            return
    elif choice in group_names:
        asset_group_name = choice
    else:
        print(_("\nInvalid selection."), file=sys.stderr)
        return

    try:
        filters_text = input(_("Filters (empty for all): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if filters_text:
        try:
            filters = shlex.split(filters_text)
        except ValueError as error:
            print(
                _("\nInvalid filter syntax: {error}").format(error=error),
                file=sys.stderr,
            )
            return
    else:
        filters = None

    try:
        force_text = input(_("Overwrite existing assets? [y/N]: ")).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    force = force_text in ("y", "yes")
    command_install_assets(asset_group_name, filters, force=force)
    print(_("\nAsset installation complete."))
