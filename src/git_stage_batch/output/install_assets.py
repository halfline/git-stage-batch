"""Human-readable install-assets rendering."""

from __future__ import annotations

from collections.abc import Collection
import sys

from ..data.asset_catalog import AssetGroup
from ..i18n import _


def print_group_install_summary(
    group: AssetGroup,
    installed_entry_names: Collection[str],
) -> None:
    """Print a summary of what was installed for one asset group."""
    installed_names = ", ".join(installed_entry_names)
    if len(installed_entry_names) == 1:
        print(
            _("✓ Installed {kind} '{name}'").format(
                kind=group.display_name_singular,
                name=next(iter(installed_entry_names)),
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
