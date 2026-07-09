"""Main TUI module for interactive mode."""

from __future__ import annotations

import sys
from ..data.batch_hunk_display import cache_batch_as_single_hunk
from ..data.progress import get_hunk_counts
from ..data.line_state import load_line_changes_from_state
from ..data.session import session_is_active
from ..exceptions import BypassRefresh, CommandError, QuitInteractive
from ..i18n import _
from ..output.colors import Colors
from ..output.hunk import print_line_level_changes
from ..utils.file_io import write_text_file_contents
from ..utils.git_command import run_git_command
from ..utils.paths import (
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from .action_dispatch import dispatch_action
from .display import print_status_bar
from .flow import FlowLocation, LocationRole, FlowState
from .prompts import (
    prompt_action,
)


def start_interactive_mode() -> None:
    """
    Start interactive TUI mode for hunk-by-hunk staging.

    Auto-initializes session, records start state, displays hunks with
    progress information, prompts for actions, and handles smart quit.

    If no changes exist, enters degraded mode where hunk-based actions
    are disabled but help, shell commands, and abort remain available.
    """
    # Import commands locally to avoid circular dependency
    from ..commands.start import command_start

    session_was_active = session_is_active()

    # Auto-initialize session (allow degraded mode if no changes)
    degraded_mode = False
    try:
        command_start(quiet=True, auto_advance=True)
    except CommandError as e:
        if e.exit_code == 2:
            degraded_mode = True
            print(_("No changes to stage."), file=sys.stderr)
        else:
            raise

    # Record start HEAD and index tree for smart quit detection (if not degraded)
    if not degraded_mode:
        head_result = run_git_command(["rev-parse", "HEAD"], requires_index_lock=False)
        write_text_file_contents(get_start_head_file_path(), head_result.stdout.strip())

        index_tree_result = run_git_command(["write-tree"], requires_index_lock=False)
        write_text_file_contents(get_start_index_tree_file_path(), index_tree_result.stdout.strip())

    use_color = Colors.enabled()
    should_refresh = True
    displayed_any_hunk = False

    # Flow state - tracks source and target for operations
    flow_state = FlowState(
        source=FlowLocation.WORKING_TREE,
        target=FlowLocation.STAGING_AREA,
        stop_session_on_quit=not session_was_active,
    )

    # Main interactive loop
    while True:
        # Load hunks based on source
        if flow_state.source.role is LocationRole.BATCH:
            # Load batch as single hunk
            rendered = cache_batch_as_single_hunk(flow_state.source.batch_name)
            line_changes = rendered.line_changes if rendered is not None else None
            gutter_mapping = rendered.gutter_to_selection_id if rendered is not None else None
        else:
            # Load working tree hunks
            line_changes = load_line_changes_from_state()
            gutter_mapping = None

        if line_changes is None:
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
        if should_refresh and line_changes is not None:
            displayed_any_hunk = True
            # Get progress stats
            stats = get_hunk_counts()

            # Display status bar
            print()
            print_status_bar(stats, flow_state)
            print()

            # Display selected hunk with line IDs
            print_line_level_changes(line_changes, gutter_to_selection_id=gutter_mapping)

        # Prompt for action
        action = prompt_action(
            use_color=use_color,
            show_question=should_refresh,
            has_hunk=(line_changes is not None)
        )

        try:
            dispatch_action(
                action,
                has_hunk=(line_changes is not None),
                use_color=use_color,
                flow_state=flow_state
            )
            should_refresh = True
        except BypassRefresh:
            should_refresh = False
        except QuitInteractive:
            break
