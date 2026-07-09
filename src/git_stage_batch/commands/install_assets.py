"""Install bundled assistant assets into the current repository."""

from __future__ import annotations

from ..data.asset_install_plan import plan_asset_installs as _plan_asset_installs
from ..data.asset_installation import copy_asset_tree as _copy_asset_tree
from ..data.asset_selection import select_asset_entries as _select_asset_entries
from ..output.install_assets import (
    print_group_install_summary as _print_group_install_summary,
)
from ..utils.git_repository import get_git_repository_root_path, require_git_repository


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
    planned_installs = _plan_asset_installs(
        selected_entries_by_group,
        repo_root,
        force=force,
    )

    for planned_install in planned_installs:
        _copy_asset_tree(planned_install.source, planned_install.destination)

    for selected_group in selected_entries_by_group:
        _print_group_install_summary(
            selected_group.group,
            selected_group.entries.keys(),
        )
