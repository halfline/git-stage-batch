"""Drop batch command implementation."""

from __future__ import annotations

from ..batch import delete_batch
import sys
from ..i18n import _
from ..utils.git import require_git_repository


def command_drop_batch(batch_name: str) -> None:
    """Delete a batch."""
    require_git_repository()
    delete_batch(batch_name)
    print(_("✓ Deleted batch '{name}'").format(name=batch_name), file=sys.stderr)
