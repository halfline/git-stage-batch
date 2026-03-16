"""Main TUI module for interactive mode."""

from __future__ import annotations

import sys
import shlex

from .i18n import _, ngettext
from .commands import (
    command_start,
    command_stop,
    command_abort,
    command_include,
    command_skip,
    command_discard,
    command_include_line,
    command_skip_line,
    command_discard_line,
    find_and_cache_next_unblocked_hunk,
    load_current_lines_from_state,
)
from . import cli
from .display import Colors, print_annotated_hunk_with_aligned_gutter
from .state import (
    CommandError,
    get_start_head_file_path,
    get_start_index_tree_file_path,
    get_hunk_counts,
    run_git_command,
    write_text_file_contents,
    read_text_file_contents,
)
from .display import Colors as DisplayColors, format_hotkey
from .tui_display import print_status_bar
from .tui_prompts import prompt_action, prompt_quit_session, prompt_line_ids, confirm_destructive_operation


class NoMoreHunks(Exception):
    """Raised when there are no more hunks to process."""
    pass


def start_interactive_mode() -> None:
    """
    Start interactive TUI mode for hunk-by-hunk staging.

    Auto-initializes session, records start state, displays hunks with
    progress information, prompts for actions, and handles smart quit.
    """
    # Auto-initialize session
    try:
        command_start()
    except CommandError as e:
        # No hunks to stage - exit cleanly
        if e.exit_code == 2:
            sys.exit(2)
        raise

    # Record start HEAD and index tree for smart quit detection
    head_result = run_git_command(["rev-parse", "HEAD"])
    write_text_file_contents(get_start_head_file_path(), head_result.stdout.strip())

    index_tree_result = run_git_command(["write-tree"])
    write_text_file_contents(get_start_index_tree_file_path(), index_tree_result.stdout.strip())

    use_color = Colors.enabled()
    should_refresh = True

    # Main interactive loop
    try:
        while True:
            # Load current hunk
            current_lines = load_current_lines_from_state()
            if current_lines is None:
                break

            # Display hunk if needed
            if should_refresh:
                # Get progress stats
                stats = get_hunk_counts()

                # Display status bar
                print()
                print_status_bar(stats)
                print()

                # Display current hunk
                print_annotated_hunk_with_aligned_gutter(current_lines)

            # Prompt for action
            action = prompt_action(use_color=use_color, show_question=should_refresh)

            # Handle action
            if action == "i":
                command_include()
                # Commands advance themselves, just check if there are more hunks
                current_lines = load_current_lines_from_state()
                if current_lines is None:
                    raise NoMoreHunks()
                should_refresh = True
            elif action == "s":
                command_skip()
                # Commands advance themselves, just check if there are more hunks
                current_lines = load_current_lines_from_state()
                if current_lines is None:
                    raise NoMoreHunks()
                should_refresh = True
            elif action == "d":
                from .tui_prompts import confirm_destructive_operation
                if confirm_destructive_operation("discard", _("This will remove the hunk from your working tree.")):
                    command_discard()
                    # Commands advance themselves, just check if there are more hunks
                    current_lines = load_current_lines_from_state()
                    if current_lines is None:
                        raise NoMoreHunks()
                    should_refresh = True
                else:
                    # Canceled, redisplay
                    should_refresh = True
            elif action == "l":
                # Line selection submenu
                handle_line_selection()
                # Redisplay current hunk after line operations
                should_refresh = True
            elif action == "q":
                handle_quit()
                break
            elif action == "?":
                print_help()
                should_refresh = False
            elif action == "":
                # Empty input, redisplay
                should_refresh = True
            else:
                # Try to parse as CLI command (escape hatch)
                try:
                    # Split the input into arguments
                    args_list = shlex.split(action)

                    # Parse arguments (quiet=True suppresses error output in TUI)
                    args = cli.parse_command_line(args_list, quiet=True)

                    if args is not None:
                        # Execute the command (CLI commands handle their own state advancement)
                        cli.dispatch_args(args)

                    # CLI commands manage their own output and state
                    should_refresh = False
                except Exception as e:
                    print(_("\nError executing command: {error}").format(error=e))
                    should_refresh = False
    except NoMoreHunks:
        # All hunks processed, go through smart quit
        handle_quit()


def handle_line_selection() -> None:
    """
    Handle line selection submenu.

    Prompts user for action and line IDs, then executes the operation.
    Returns after operation or on cancel (Ctrl-C).
    """
    use_color = Colors.enabled()

    # Get current lines to show available line IDs
    current_lines = load_current_lines_from_state()
    if current_lines is None:
        return

    # Collect changed line IDs
    changed_ids = [str(line.id) for line in current_lines.lines if line.id is not None]
    if not changed_ids:
        print(_("\nNo changed lines in this hunk."))
        return

    # Show available line IDs
    ids_text = ", ".join(changed_ids)
    ids_display = _("\nChanged line IDs: {ids}").format(ids=ids_text)
    if use_color:
        print(f"{DisplayColors.CYAN}{ids_display}{DisplayColors.RESET}")
    else:
        print(ids_display)

    # Prompt for action
    print()
    try:
        if use_color:
            action_input = input(f"{_('Action for lines')} {format_hotkey('include', 'i', DisplayColors.GREEN)}, "
                                f"{format_hotkey('skip', 's', '')}, "
                                f"{format_hotkey('discard', 'd', DisplayColors.RED)}? ").strip().lower()
        else:
            action_input = input(_("Action for lines [i]nclude, [s]kip, [d]iscard? ")).strip().lower()
    except (KeyboardInterrupt, EOFError):
        # Canceled, return to main loop
        return

    # Normalize action
    action_map = {"i": "include", "include": "include", "s": "skip", "skip": "skip", "d": "discard", "discard": "discard"}
    action = action_map.get(action_input)

    if action is None:
        print(_("\nUnknown action: '{action}'").format(action=action_input))
        return

    # Prompt for line IDs
    line_ids = prompt_line_ids()
    if not line_ids:
        # Canceled or empty, return to main loop
        return

    # Execute operation
    try:
        if action == "include":
            command_include_line(line_ids)
        elif action == "skip":
            command_skip_line(line_ids)
        elif action == "discard":
            if confirm_destructive_operation("discard", _("This will remove lines {line_ids} from your working tree.").format(line_ids=line_ids)):
                command_discard_line(line_ids)
    except Exception as e:
        print(_("\nError: {error}").format(error=e))


def handle_quit() -> None:
    """
    Handle quit action with smart quit logic.

    Checks if any changes were made (HEAD, index tree, or discards).
    If no changes, silently stops. If changes exist, prompts user.
    """
    # Check if any changes were made
    start_head_file = get_start_head_file_path()
    start_index_tree_file = get_start_index_tree_file_path()

    if not start_head_file.exists() or not start_index_tree_file.exists():
        # No start state recorded, just stop
        command_stop()
        return

    start_head = read_text_file_contents(start_head_file).strip()
    start_index_tree = read_text_file_contents(start_index_tree_file).strip()

    # Check current state
    current_head = run_git_command(["rev-parse", "HEAD"]).stdout.strip()
    current_index_tree = run_git_command(["write-tree"]).stdout.strip()

    # Check if any discards happened
    stats = get_hunk_counts()
    has_discards = stats.get("discarded", 0) > 0

    # If nothing changed, silently stop
    if current_head == start_head and current_index_tree == start_index_tree and not has_discards:
        command_stop()
        return

    # Changes exist, prompt user
    choice = prompt_quit_session()

    if choice == "keep":
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
    print(_("  l, lines     - Select specific lines from this hunk"))
    print(_("  f, file      - Include or skip all hunks in this file"))
    print(_("  ?, help      - Show this help message"))
    print()
