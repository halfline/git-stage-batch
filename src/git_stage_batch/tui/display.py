"""TUI-specific display utilities for interactive mode."""

from __future__ import annotations

from ..output import Colors
from ..i18n import _


def print_status_bar(stats: dict[str, int]) -> None:
    """
    Print a status bar showing progress statistics.

    Args:
        stats: Dictionary with keys 'included', 'skipped', 'discarded'

    Format:
        ════════════════════════════════════════════════════════════════
        Included: 5 │ Skipped: 2 │ Discarded: 1
        ════════════════════════════════════════════════════════════════
    """
    use_color = Colors.enabled()

    # Build status line
    parts = [
        _("Included: {count}").format(count=stats.get('included', 0)),
        _("Skipped: {count}").format(count=stats.get('skipped', 0)),
        _("Discarded: {count}").format(count=stats.get('discarded', 0)),
    ]
    status_line = " │ ".join(parts)

    # Box drawing characters for separator
    separator = "═" * 64

    if use_color:
        print(f"{Colors.CYAN}{separator}{Colors.RESET}")
        print(f"{Colors.BOLD}{status_line}{Colors.RESET}")
        print(f"{Colors.CYAN}{separator}{Colors.RESET}")
    else:
        print(separator)
        print(status_line)
        print(separator)


def print_action_summary(action: str, details: str = "") -> None:
    """
    Print a summary of the action just performed.

    Args:
        action: The action performed (e.g., "Staged hunk", "Skipped file")
        details: Optional additional details (e.g., "(3 hunks)" for file operations)

    Examples:
        - "✓ Staged hunk"
        - "✓ Skipped file (3 hunks)"
        - "✓ Discarded hunk"
    """
    use_color = Colors.enabled()

    # Determine color based on action type
    if "staged" in action.lower() or "included" in action.lower():
        color = Colors.GREEN
    elif "skip" in action.lower():
        color = Colors.CYAN
    elif "discard" in action.lower():
        color = Colors.RED
    else:
        color = Colors.RESET

    # Build summary
    checkmark = "✓"
    if details:
        summary = f"{checkmark} {action} {details}"
    else:
        summary = f"{checkmark} {action}"

    if use_color:
        print(f"{color}{summary}{Colors.RESET}")
    else:
        print(summary)
