"""New batch command implementation."""

from __future__ import annotations

from ..batch import create_batch
import sys
from ..i18n import _
from ..utils.git import require_git_repository


def command_new_batch(batch_name: str, note: str = "") -> None:
    """Create a new batch."""
    require_git_repository()
    create_batch(batch_name, note)
    print(_("✓ Created batch '{name}'").format(name=batch_name), file=sys.stderr)
