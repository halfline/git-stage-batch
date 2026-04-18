"""Annotate batch command implementation."""

from __future__ import annotations

from ..batch import update_batch_note
import sys
from ..data.undo import undo_checkpoint
from ..i18n import _
from ..utils.git import require_git_repository


def command_annotate_batch(batch_name: str, note: str) -> None:
    """Add or update batch description."""
    require_git_repository()
    with undo_checkpoint(f"annotate {batch_name}"):
        update_batch_note(batch_name, note)
    print(_("✓ Updated note for batch '{name}'").format(name=batch_name), file=sys.stderr)
