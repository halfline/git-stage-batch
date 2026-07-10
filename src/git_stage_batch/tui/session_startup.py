"""Interactive session startup handling."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from ..data.session import session_is_active
from ..exceptions import CommandError
from ..i18n import _
from ..utils.file_io import write_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.paths import (
    get_start_head_file_path,
    get_start_index_tree_file_path,
)


@dataclass(frozen=True)
class InteractiveSessionStartup:
    """Startup state needed by the interactive loop."""

    degraded_mode: bool
    session_was_active: bool


def prepare_interactive_session() -> InteractiveSessionStartup:
    """Start an interactive session and capture initial repository state."""
    from ..commands.start import command_start

    session_was_active = session_is_active()
    degraded_mode = False

    try:
        command_start(quiet=True, auto_advance=True)
    except CommandError as error:
        if error.exit_code != 2:
            raise
        degraded_mode = True
        print(_("No changes to stage."), file=sys.stderr)

    if not degraded_mode:
        _record_start_repository_state()

    return InteractiveSessionStartup(
        degraded_mode=degraded_mode,
        session_was_active=session_was_active,
    )


def _record_start_repository_state() -> None:
    head_result = run_git_command(["rev-parse", "HEAD"], requires_index_lock=False)
    write_text_file_contents(get_start_head_file_path(), head_result.stdout.strip())

    index_tree_result = run_git_command(["write-tree"], requires_index_lock=False)
    write_text_file_contents(
        get_start_index_tree_file_path(),
        index_tree_result.stdout.strip(),
    )
