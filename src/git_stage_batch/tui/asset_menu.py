"""Bundled-asset installation menu for interactive mode."""

from __future__ import annotations

import shlex
import sys

from ..commands.install_assets import command_install_assets
from ..data.asset_catalog import ASSET_GROUPS, asset_group_menu_label
from ..i18n import _, bidi_isolate, pgettext
from ..output.colors import format_hotkey
from .action_prompt_choices import (
    localized_word_aliases,
    normalize_localized_choice,
)
from .prompts import unlocked_input


def handle_asset_menu() -> None:
    """Prompt for assistant asset install options and run the installer."""
    group_names = list(ASSET_GROUPS)
    group_labels = {
        group_name: asset_group_menu_label(ASSET_GROUPS[group_name])
        for group_name in group_names
    }

    print()
    print(_("Install bundled assistant assets:"))
    print("  {} {}".format(bidi_isolate("[1]"), _("all asset groups")))
    for idx, group_name in enumerate(group_names, 2):
        print(f"  {bidi_isolate(f'[{idx}]')} {group_labels[group_name]}")

    try:
        choice = unlocked_input(_("Group (empty to cancel): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not choice:
        return

    asset_group_name: str | None
    normalized_group = normalize_localized_choice(
        choice,
        stable_codes=frozenset({"1"}),
        legacy_words={"all": "1", "all asset groups": "1"},
        localized_words=localized_word_aliases(
            (
                (str(_("all asset groups")), "1"),
                *((label, group_name) for group_name, label in group_labels.items()),
            )
        ),
    )
    if normalized_group == "1":
        asset_group_name = None
    elif choice.isdigit():
        group_idx = int(choice) - 2
        if 0 <= group_idx < len(group_names):
            asset_group_name = group_names[group_idx]
        else:
            print(_("\nInvalid selection."), file=sys.stderr)
            return
    elif normalized_group in group_names:
        asset_group_name = normalized_group
    else:
        print(_("\nInvalid selection."), file=sys.stderr)
        return

    try:
        filters_text = unlocked_input(_("Filters (empty for all): ")).strip()
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

    yes_key = pgettext("yes hotkey", "y")
    no_key = pgettext("no hotkey", "n")
    yes_text = _("yes")
    no_text = _("no")
    try:
        force_text = unlocked_input(
            _("Overwrite existing assets? {yes} / {no}: ").format(
                yes=format_hotkey(yes_text, yes_key),
                no=format_hotkey(no_text, no_key),
            )
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return

    force_choice = normalize_localized_choice(
        force_text,
        stable_codes=frozenset({"y", "n"}),
        legacy_words={"yes": "y", "no": "n"},
        localized_words=localized_word_aliases(
            (
                (str(yes_key), "y"),
                (str(no_key), "n"),
                (str(yes_text), "y"),
                (str(no_text), "n"),
            )
        ),
    )
    force = force_choice == "y"
    command_install_assets(asset_group_name, filters, force=force)
    print(_("\nAsset installation complete."))
