"""Main TUI module for interactive mode."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable
from ..data.batch_hunk_display import cache_batch_as_single_hunk
from ..data.file_tracking import auto_add_untracked_files
from ..data.hunk_tracking import fetch_next_change
from ..data.progress import get_hunk_counts
from ..data.line_state import load_line_changes_from_state
from ..data.session import session_is_active
from ..exceptions import BypassRefresh, CommandError, QuitInteractive
from ..i18n import _
from ..output.colors import Colors
from ..output.hunk import print_line_level_changes
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.paths import (
    get_selected_hunk_hash_file_path,
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from .asset_menu import handle_asset_menu
from .batch_menu import handle_batch_menu
from .display import print_status_bar
from .file_selection_menu import handle_file_selection_menu
from .file_review import handle_current_file_review, handle_file_browser
from .flow import FlowLocation, LocationRole, FlowState
from .flow_menu import handle_from_menu, handle_to_menu
from .line_selection_menu import handle_line_selection_menu
from .prompts import (
    confirm_destructive_operation,
    prompt_action,
    prompt_fixup_action,
    prompt_quit_session,
    prompt_shell_command,
)


@dataclass
class ActionHandler:
    """Configuration for an interactive action."""
    needs_hunk: bool
    handler: Callable[[FlowState], None]


def _handle_include(flow_state: FlowState) -> None:
    """Handle include action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            from ..commands.include import command_include
            command_include(quiet=True, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            # Include to batch (via skip-to-batch)
            from ..commands.include import command_include_to_batch
            command_include_to_batch(
                flow_state.target.batch_name,
                quiet=True,
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
    elif flow_state.source.role is LocationRole.BATCH:
        # Include from batch
        if flow_state.target.role is not LocationRole.STAGING_AREA:
            print(_("Batch-to-batch transfers not yet supported. Target must be staging."), file=sys.stderr)
            raise BypassRefresh()
        from ..commands.include_from import command_include_from_batch
        command_include_from_batch(flow_state.source.batch_name)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def _handle_skip(flow_state: FlowState) -> None:
    """Handle skip action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            from ..commands.skip import command_skip
            command_skip(quiet=True, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            # Skip to batch
            from ..commands.include import command_include_to_batch
            command_include_to_batch(
                flow_state.target.batch_name,
                quiet=True,
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
    elif flow_state.source.role is LocationRole.BATCH:
        # Skip doesn't make sense when pulling from batch
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        raise BypassRefresh()
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def _handle_discard(flow_state: FlowState) -> None:
    """Handle discard action based on source and target."""
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            from ..commands.discard import command_discard
            if confirm_destructive_operation("discard", _("This will remove the hunk from your working tree.")):
                command_discard(quiet=True, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            # Discard to batch (save for later)
            from ..commands.discard import command_discard_to_batch
            command_discard_to_batch(
                flow_state.target.batch_name,
                quiet=True,
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
    elif flow_state.source.role is LocationRole.BATCH:
        # Discard from batch
        if flow_state.target.role is not LocationRole.STAGING_AREA:
            print(_("Batch-to-batch transfers not yet supported. Target must be staging."), file=sys.stderr)
            raise BypassRefresh()
        from ..commands.discard_from import command_discard_from_batch
        command_discard_from_batch(flow_state.source.batch_name)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


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
    handle_fixup_selection()


def _handle_quit(flow_state: FlowState) -> None:
    """Handle quit action."""
    handle_quit(stop_session=flow_state.stop_session_on_quit)
    raise QuitInteractive()


def _handle_shell(action: str) -> None:
    """Handle shell command execution."""
    if len(action) > 1:
        command = action[1:].strip()
    else:
        command = prompt_shell_command()

    if command:
        result = subprocess.run(
            command,
            shell=True,
            cwd=get_git_repository_root_path(),
        )
        if result.returncode != 0:
            print(_("Command exited with status {}").format(result.returncode))

        try:
            input(_("\nPress Enter to continue..."))
        except (KeyboardInterrupt, EOFError):
            pass
    else:
        print(_("No command entered"))


def _handle_batch(flow_state: FlowState) -> None:
    """Handle batch management submenu."""
    handle_batch_menu()


def _handle_help(flow_state: FlowState) -> None:
    """Handle help action."""
    print_help()
    raise BypassRefresh()


def _handle_cli_command(action: str) -> None:
    """Handle arbitrary CLI command as escape hatch."""
    try:
        from ..cli.argument_parser import parse_command_line
        from ..cli.execution import execute_non_interactive_args

        args_list = shlex.split(action)
        args = parse_command_line(args_list, quiet=False)

        if args is not None:
            if (
                getattr(args, "interactive_flag", False)
                or getattr(args, "interactive_command", False)
            ):
                print(_("\nAlready in interactive mode."))
            else:
                execute_non_interactive_args(args)
        else:
            print(_("\nUnknown command: '{cmd}'").format(cmd=action))
            print_help()
    except Exception as e:
        print(_("\nError executing command: {error}").format(error=e))
    raise BypassRefresh()


# Map of actions to their handlers
ACTION_HANDLERS = {
    "i": ActionHandler(needs_hunk=True, handler=_handle_include),
    "s": ActionHandler(needs_hunk=True, handler=_handle_skip),
    "d": ActionHandler(needs_hunk=True, handler=_handle_discard),
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
        _handle_shell(action)
        return

    if action == "":
        return

    # Handle flow actions specially - they modify flow_state in place
    if action.startswith("<"):
        if len(action) > 1:
            # Direct batch selection: <batch-name
            batch_name = action[1:]
            flow_state.source = FlowLocation.for_batch(batch_name)
            # Prevent batch-to-batch state
            if flow_state.target.role is LocationRole.BATCH:
                flow_state.target = FlowLocation.STAGING_AREA
        else:
            # Show menu
            handle_from_menu(flow_state)
        return  # Refresh display after flow change
    elif action.startswith(">"):
        if len(action) > 1:
            # Direct batch selection: >batch-name
            batch_name = action[1:]
            flow_state.target = FlowLocation.for_batch(batch_name)
            # Prevent batch-to-batch state
            if flow_state.source.role is LocationRole.BATCH:
                flow_state.source = FlowLocation.WORKING_TREE
        else:
            # Show menu
            handle_to_menu(flow_state)
        return  # Refresh display after flow change

    handler_config = ACTION_HANDLERS.get(action)

    if handler_config is not None:
        if handler_config.needs_hunk and not has_hunk:
            print(_("No changes to process"), file=sys.stderr)
            raise BypassRefresh()

        handler_config.handler(flow_state)
        return

    _handle_cli_command(action)


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


def handle_fixup_selection() -> None:
    """
    Handle suggest-fixup submenu for iterative candidate selection.

    Displays fixup candidates one at a time, prompting user to accept,
    move to next, reset, or cancel. Maintains iteration state across
    invocations within the same hunk context.
    """
    from ..commands.suggest_fixup import command_suggest_fixup
    from ..data.suggest_fixup_state import (
        clear_suggest_fixup_state,
        read_suggest_fixup_state,
    )

    use_color = Colors.enabled()
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    # Show initial candidate
    try:
        command_suggest_fixup()
    except CommandError as e:
        # No candidates found or other error
        print(f"\n{e.message}")
        return

    # Interactive loop for fixup candidate selection
    while True:
        print()
        action = prompt_fixup_action(use_color=use_color)

        if action == "y":
            # Accept - show how to create fixup commit
            state = read_suggest_fixup_state()
            if state and state.get("last_shown_commit"):
                commit_hash = state["last_shown_commit"][:7]
                print()
                print(_("Create fixup commit with:"))
                if use_color:
                    print(f"  {Colors.BOLD}git commit --fixup={commit_hash}{Colors.RESET}")
                else:
                    print(f"  git commit --fixup={commit_hash}")
                print()
            break
        elif action == "n":
            # Next candidate
            try:
                command_suggest_fixup()
            except CommandError as e:
                print(f"\n{e.message}")
                break
        elif action == "r":
            # Reset iteration
            try:
                command_suggest_fixup(reset=True)
            except CommandError as e:
                print(f"\n{e.message}")
                break
        elif action == "q":
            # Cancel - abort and exit submenu
            clear_suggest_fixup_state()
            print(_("\nCanceled."))
            break
        else:
            print(_("\nUnknown action: '{action}'").format(action=action))


def handle_quit(*, stop_session: bool = True) -> None:
    """
    Handle quit action with smart quit logic.

    Checks if any changes were made (HEAD, index tree, or discards).
    If no changes, silently stops. If changes exist, prompts user.
    """
    from ..commands.stop import command_stop
    from ..commands.abort import command_abort

    print()  # Move to new line after Action: prompt

    # Check if any changes were made
    start_head_file = get_start_head_file_path()
    start_index_tree_file = get_start_index_tree_file_path()

    if not start_head_file.exists() or not start_index_tree_file.exists():
        # No start state recorded, just stop
        if stop_session:
            command_stop()
        return

    start_head = read_text_file_contents(start_head_file).strip()
    start_index_tree = read_text_file_contents(start_index_tree_file).strip()

    # Check selected state
    selected_head = run_git_command(["rev-parse", "HEAD"], requires_index_lock=False).stdout.strip()
    selected_index_tree = run_git_command(["write-tree"], requires_index_lock=False).stdout.strip()

    # Check if any discards happened
    stats = get_hunk_counts()
    has_discards = stats.get("discarded", 0) > 0

    # If nothing changed, silently stop
    if selected_head == start_head and selected_index_tree == start_index_tree and not has_discards:
        if stop_session:
            command_stop()
        return

    # Changes exist, prompt user
    choice = prompt_quit_session()

    if choice == "keep":
        if stop_session:
            command_stop()
    elif choice == "undo":
        command_abort()
    else:  # cancel
        # Return to main loop (don't exit)
        return


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
