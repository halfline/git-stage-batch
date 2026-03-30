"""TUI-specific display utilities for interactive mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..output import Colors
from ..i18n import _

if TYPE_CHECKING:
    from .flow import FlowState


def print_status_bar(stats: dict[str, int], flow_state: FlowState) -> None:
    """
    Print a status bar showing progress statistics and flow state.

    Args:
        stats: Dictionary with keys 'included', 'skipped', 'discarded'
        flow_state: Current flow state (source and target)

    Format:
        ════════════════════════════════════════════════════════════════
        Source: working tree → Target: staging
        Included: 5  Skipped: 2  Discarded: 1
        ════════════════════════════════════════════════════════════════
    """
    use_color = Colors.enabled()

    # Build flow line with bold labels, gray arrow
    if use_color:
        flow_line = _("{source_label}{source} {arrow} {target_label}{target}").format(
            source_label=f"{Colors.BOLD}Source:{Colors.RESET} ",
            source=flow_state.source.get_display_label(),
            arrow=f"{Colors.GRAY}→{Colors.RESET}",
            target_label=f"{Colors.BOLD}Target:{Colors.RESET} ",
            target=flow_state.target.get_display_label()
        )
    else:
        flow_line = _("Source: {source} → Target: {target}").format(
            source=flow_state.source.get_display_label(),
            target=flow_state.target.get_display_label()
        )

    # Build stats line with bold labels
    if use_color:
        stats_parts = [
            f"{Colors.BOLD}Included:{Colors.RESET} {stats.get('included', 0)}",
            f"{Colors.BOLD}Skipped:{Colors.RESET} {stats.get('skipped', 0)}",
            f"{Colors.BOLD}Discarded:{Colors.RESET} {stats.get('discarded', 0)}",
        ]
    else:
        stats_parts = [
            _("Included: {count}").format(count=stats.get('included', 0)),
            _("Skipped: {count}").format(count=stats.get('skipped', 0)),
            _("Discarded: {count}").format(count=stats.get('discarded', 0)),
        ]
    stats_line = "  ".join(stats_parts)

    # Box drawing characters for separator
    separator = "═" * 64

    if use_color:
        print(f"{Colors.CYAN}{separator}{Colors.RESET}")
        print(flow_line)
        print(stats_line)
    else:
        print(separator)
        print(flow_line)
        print(stats_line)


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
