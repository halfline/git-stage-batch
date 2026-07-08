"""Help display for interactive mode."""

from __future__ import annotations

from ..i18n import _
from ..output.colors import Colors


def print_help() -> None:
    """Print help text for interactive mode."""
    use_color = Colors.enabled()

    print()
    header = _("Interactive Mode Commands:")
    if use_color:
        print(f"{Colors.BOLD}{header}{Colors.RESET}")
    else:
        print(header)

    print()
    print(_("Primary actions:"))
    print(_("  i, include   - Stage this hunk to the index"))
    print(_("  s, skip      - Skip this hunk for now"))
    print(_("  d, discard   - Remove this hunk from working tree (DESTRUCTIVE)"))
    print(_("  q, quit      - Exit interactive mode"))
    print()
    print(_("More options:"))
    print(_("  a, again     - Clear state and start fresh pass through skipped hunks"))
    print(_("  u, undo      - Undo the most recent operation"))
    print(_("  U, redo      - Redo the most recently undone operation"))
    print(_("  S, status    - Show session status"))
    print(_("  A, assets    - Install bundled assistant assets"))
    print(_("  l, lines     - Select specific lines from this hunk"))
    print(_("  f, file      - Include or skip all hunks in this file"))
    print(_("  v, view      - Review this whole file with page selection"))
    print(_("  o, open      - Choose a file to review"))
    print(_("  x, fixup     - Suggest which commit to fixup (iterative)"))
    print(_("  !<cmd>       - Run shell command (e.g., !git log, or just ! to prompt)"))
    print(_("  ?, help      - Show this help message"))
    print()
