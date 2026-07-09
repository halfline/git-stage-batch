"""Main TUI module for interactive mode."""

from __future__ import annotations

import sys

from ..exceptions import BypassRefresh, QuitInteractive
from ..i18n import _
from ..output.colors import Colors
from . import current_change
from .action_dispatch import dispatch_action
from .flow import FlowLocation, LocationRole, FlowState
from .prompts import (
    prompt_action,
)
from .session_startup import prepare_interactive_session


def start_interactive_mode() -> None:
    """
    Start interactive TUI mode for hunk-by-hunk staging.

    Auto-initializes session, records start state, displays hunks with
    progress information, prompts for actions, and handles smart quit.

    If no changes exist, enters degraded mode where hunk-based actions
    are disabled but help, shell commands, and abort remain available.
    """
    startup = prepare_interactive_session()
    degraded_mode = startup.degraded_mode

    use_color = Colors.enabled()
    should_refresh = True
    displayed_any_hunk = False

    # Flow state - tracks source and target for operations
    flow_state = FlowState(
        source=FlowLocation.WORKING_TREE,
        target=FlowLocation.STAGING_AREA,
        stop_session_on_quit=not startup.session_was_active,
    )

    # Main interactive loop
    while True:
        loaded_change = current_change.load_current_change(flow_state)
        has_hunk = loaded_change is not None

        if loaded_change is None:
            # No hunks available - enter degraded mode
            if not degraded_mode:
                degraded_mode = True
                if displayed_any_hunk:
                    print()
                    print(_("No more hunks to process."))
                else:
                    print(_("No changes to stage."), file=sys.stderr)
        else:
            # Hunks available - exit degraded mode if we were in it
            degraded_mode = False

        # Display hunk if needed
        if should_refresh and loaded_change is not None:
            displayed_any_hunk = True
            current_change.display_current_change(loaded_change, flow_state)

        # Prompt for action
        action = prompt_action(
            use_color=use_color,
            show_question=should_refresh,
            has_hunk=has_hunk,
        )

        try:
            dispatch_action(
                action,
                has_hunk=has_hunk,
                use_color=use_color,
                flow_state=flow_state,
            )
            should_refresh = True
        except BypassRefresh:
            should_refresh = False
        except QuitInteractive:
            break
