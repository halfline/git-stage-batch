"""TUI-specific display utilities for interactive mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..output.colors import Colors
from ..i18n import _, pgettext

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
        source_label = _("Source:")
        arrow = pgettext("flow direction arrow", "→")
        target_label = _("Target:")
        flow_line = _("{source_label}{source} {arrow} {target_label}{target}").format(
            source_label=f"{Colors.BOLD}{source_label}{Colors.RESET} ",
            source=flow_state.source.get_display_label(),
            arrow=f"{Colors.GRAY}{arrow}{Colors.RESET}",
            target_label=f"{Colors.BOLD}{target_label}{Colors.RESET} ",
            target=flow_state.target.get_display_label(),
        )
    else:
        flow_line = _("Source: {source} → Target: {target}").format(
            source=flow_state.source.get_display_label(),
            target=flow_state.target.get_display_label()
        )

    # Build stats line with bold labels
    if use_color:
        included_text = _("Included: {count}").format(
            count=stats.get("included", 0)
        )
        skipped_text = _("Skipped: {count}").format(count=stats.get("skipped", 0))
        discarded_text = _("Discarded: {count}").format(
            count=stats.get("discarded", 0)
        )
        stats_parts = [
            f"{Colors.BOLD}{included_text}{Colors.RESET}",
            f"{Colors.BOLD}{skipped_text}{Colors.RESET}",
            f"{Colors.BOLD}{discarded_text}{Colors.RESET}",
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
