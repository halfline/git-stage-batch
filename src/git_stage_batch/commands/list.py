"""List batches command implementation."""

from __future__ import annotations

from ..batch import list_batch_names
from ..i18n import _
from ..utils.git import require_git_repository


def command_list_batches() -> None:
    """List all batches."""
    require_git_repository()

    batches = list_batch_names()
    if not batches:
        print(_("No batches found"))
        return

    for batch_name in batches:
        print(batch_name)
