"""Drop batch command implementation."""

from __future__ import annotations

from ..batch import delete_batch
from ..batch.source_selector import require_plain_batch_name
import sys
from ..data.undo import undo_checkpoint
from ..i18n import _
from ..utils.git import require_git_repository


def command_drop_batch(batch_name: str) -> None:
    """Delete a batch."""
    require_git_repository()
    batch_name = require_plain_batch_name(batch_name, "drop")
    with undo_checkpoint(f"drop {batch_name}"):
        delete_batch(batch_name)
    print(_("✓ Deleted batch '{name}'").format(name=batch_name), file=sys.stderr)
