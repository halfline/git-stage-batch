"""Main TUI module for interactive mode."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable
from ..data.selected_change.batch_file_cache import cache_batch_as_single_hunk
from ..data.file_tracking import auto_add_untracked_files
from ..data.hunk_tracking import fetch_next_change
from ..data.progress import get_hunk_counts
from ..data.line_state import load_line_changes_from_state
from ..data.session import session_is_active
from ..exceptions import BypassRefresh, CommandError, QuitInteractive
from ..i18n import _
from ..output.colors import Colors
from ..output.hunk import print_line_level_changes
from ..utils.file_io import write_text_file_contents
from ..utils.git import run_git_command
from ..utils.paths import (
    get_selected_hunk_hash_file_path,
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from .asset_menu import handle_asset_menu
from .batch_menu import handle_batch_menu
from .cli_escape import handle_cli_escape
from .display import print_status_bar
from .file_selection_menu import handle_file_selection_menu
from .file_review import handle_current_file_review, handle_file_browser
from .fixup_menu import handle_fixup_menu
from .flow import FlowLocation, LocationRole, FlowState
from .flow_actions import handle_flow_action
from .help_display import print_help
from .hunk_actions import (
    handle_hunk_discard,
    handle_hunk_include,
    handle_hunk_skip,
)
from .line_selection_menu import handle_line_selection_menu
from .prompts import (
    prompt_action,
)
from .session_quit import handle_quit
from .shell_command import handle_shell_command


@dataclass
class ActionHandler:
    """Configuration for an interactive action."""
    needs_hunk: bool
    handler: Callable[[FlowState], None]


def _handle_again(flow_state: FlowState) -> None:
    """Handle again action - restart from first hunk."""
    # Clear selected hunk position to restart from beginning
    # Don't use command_again() as it destroys abort state and start state
    hunk_hash_file = get_selected_hunk_hash_file_path()
    if hunk_hash_file.exists():
        hunk_hash_file.unlink()

    # Re-scan for untracked files
    auto_add_untracked_files()

    fetch_next_change()


def _handle_undo(flow_state: FlowState) -> None:
    """Handle undo action."""
    from ..commands.undo import command_undo
    command_undo()


def _handle_redo(flow_state: FlowState) -> None:
    """Handle redo action."""
    from ..commands.redo import command_redo
    command_redo()


def _handle_status(flow_state: FlowState) -> None:
    """Handle status drawer."""
    from ..commands.status import command_status
    command_status()
    raise BypassRefresh()


def _handle_assets(flow_state: FlowState) -> None:
    """Handle bundled assistant asset installation."""
    handle_asset_menu()
    raise BypassRefresh()


def _handle_line_selection(flow_state: FlowState) -> None:
    """Handle line selection submenu."""
    handle_line_selection_menu(flow_state)


def _handle_file_selection(flow_state: FlowState) -> None:
    """Handle file selection submenu."""
    handle_file_selection_menu(flow_state)


def _handle_file_review(flow_state: FlowState) -> None:
    """Handle current-file review browser."""
    handle_current_file_review(flow_state)


def _handle_file_browser(flow_state: FlowState) -> None:
    """Handle review file chooser."""
    handle_file_browser(flow_state)


def _handle_fixup(flow_state: FlowState) -> None:
    """Handle fixup submenu."""
    if flow_state.source.role is LocationRole.BATCH:
        # Fixup doesn't make sense when pulling from batch
        print(_("Suggest-fixup is not available when pulling from a batch."), file=sys.stderr)
        raise BypassRefresh()
    handle_fixup_menu()


def _handle_quit(flow_state: FlowState) -> None:
    """Handle quit action."""
    handle_quit(stop_session=flow_state.stop_session_on_quit)
    raise QuitInteractive()


def _handle_batch(flow_state: FlowState) -> None:
    """Handle batch management submenu."""
    handle_batch_menu()


def _handle_help(flow_state: FlowState) -> None:
    """Handle help action."""
    print_help()
    raise BypassRefresh()


# Map of actions to their handlers
ACTION_HANDLERS = {
    "i": ActionHandler(needs_hunk=True, handler=handle_hunk_include),
    "s": ActionHandler(needs_hunk=True, handler=handle_hunk_skip),
    "d": ActionHandler(needs_hunk=True, handler=handle_hunk_discard),
    "l": ActionHandler(needs_hunk=True, handler=_handle_line_selection),
    "f": ActionHandler(needs_hunk=True, handler=_handle_file_selection),
    "v": ActionHandler(needs_hunk=True, handler=_handle_file_review),
    "o": ActionHandler(needs_hunk=False, handler=_handle_file_browser),
    "x": ActionHandler(needs_hunk=True, handler=_handle_fixup),
    "a": ActionHandler(needs_hunk=False, handler=_handle_again),
    "u": ActionHandler(needs_hunk=False, handler=_handle_undo),
    "U": ActionHandler(needs_hunk=False, handler=_handle_redo),
    "S": ActionHandler(needs_hunk=False, handler=_handle_status),
    "A": ActionHandler(needs_hunk=False, handler=_handle_assets),
    "b": ActionHandler(needs_hunk=False, handler=_handle_batch),
    "?": ActionHandler(needs_hunk=False, handler=_handle_help),
    "q": ActionHandler(needs_hunk=False, handler=_handle_quit),
}


def _dispatch_action(
    action: str,
    has_hunk: bool,
    use_color: bool,
    flow_state: FlowState
) -> None:
    """
    Dispatch an action to its handler.

    Raises QuitInteractive to exit or BypassRefresh to skip display update.
    """
    if action.startswith("!"):
        handle_shell_command(action)
        return

    if action == "":
        return

    if handle_flow_action(action, flow_state):
        return

    handler_config = ACTION_HANDLERS.get(action)

    if handler_config is not None:
        if handler_config.needs_hunk and not has_hunk:
            print(_("No changes to process"), file=sys.stderr)
            raise BypassRefresh()

        handler_config.handler(flow_state)
        return

    handle_cli_escape(action, print_help=print_help)


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
            _dispatch_action(
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
