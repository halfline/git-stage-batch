"""Main TUI module for interactive mode."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable
from ..batch import query as batch_query
from ..data.file_tracking import auto_add_untracked_files
from ..data.hunk_tracking import cache_batch_as_single_hunk, format_id_range
from ..data.hunk_tracking import fetch_next_change
from ..data.progress import get_hunk_counts
from ..data.line_state import load_line_changes_from_state
from ..exceptions import BypassRefresh, CommandError, QuitInteractive
from ..i18n import _
from ..output import Colors, format_hotkey, print_line_level_changes
from ..utils.file_io import read_text_file_contents, write_text_file_contents
from ..utils.git import get_git_repository_root_path, run_git_command
from ..utils.paths import (
    get_selected_hunk_hash_file_path,
    get_start_head_file_path,
    get_start_index_tree_file_path,
)
from .display import print_status_bar
from .flow import FlowLocation, LocationRole, FlowState
from .prompts import (
    confirm_destructive_operation,
    prompt_action,
    prompt_fixup_action,
    prompt_line_ids,
    prompt_quit_session,
    prompt_shell_command,
    wrap_prompt_for_readline,
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


def _handle_line_selection(flow_state: FlowState) -> None:
    """Handle line selection submenu."""
    handle_line_selection(flow_state)


def _handle_file_selection(flow_state: FlowState) -> None:
    """Handle file selection submenu."""
    handle_file_selection(flow_state)


def _handle_fixup(flow_state: FlowState) -> None:
    """Handle fixup submenu."""
    if flow_state.source.role is LocationRole.BATCH:
        # Fixup doesn't make sense when pulling from batch
        print(_("Suggest-fixup is not available when pulling from a batch."), file=sys.stderr)
        raise BypassRefresh()
    handle_fixup_selection()


def _handle_quit(flow_state: FlowState) -> None:
    """Handle quit action."""
    handle_quit()
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

    use_color = Colors.enabled()

    # Loop until user presses Ctrl-C to exit submenu
    while True:
        # Show list of existing batches
        batch_names = batch_query.list_batch_names()

        # If no batches exist, jump straight to create
        if not batch_names:
            print()
            print(_("No batches found. Create one now."))
            if not _batch_create():
                # User cancelled, exit submenu and refresh
                return
            continue

        print()
        print(_("Existing batches:"))
        for name in batch_names:
            metadata = batch_query.read_batch_metadata(name)
            note = metadata.get("note", "")
            if note:
                if use_color:
                    print(_("  {name} - {note}").format(
                        name=f"{Colors.CYAN}{name}{Colors.RESET}",
                        note=note
                    ))
                else:
                    print(_("  {name} - {note}").format(name=name, note=note))
            else:
                if use_color:
                    print(f"  {Colors.CYAN}{name}{Colors.RESET}")
                else:
                    print(f"  {name}")
        print()

        # Show batch operations menu
        print(_("Batch operations:"))
        operations = [
            (_("create"), "c", Colors.GREEN if use_color else ""),
            (_("edit"), "e", ""),
            (_("drop"), "d", Colors.RED if use_color else ""),
            (_("apply"), "a", ""),
        ]
        for text, hotkey, color in operations:
            formatted = format_hotkey(text, hotkey, color)
            print(f"  {formatted}")
        print()

        try:
            action = input(_("Select: ")).strip().lower()
        except (KeyboardInterrupt, EOFError):
            # Ctrl-C exits submenu and refreshes
            return

        # Empty input exits submenu and refreshes
        if not action:
            return

        if action in ("c", "create"):
            _batch_create()
        elif action in ("e", "edit"):
            _batch_edit()
        elif action in ("d", "drop"):
            _batch_drop()
        elif action in ("a", "apply"):
            _batch_apply()
        else:
            print(_("\nUnknown action: '{action}'").format(action=action))


def _batch_create() -> bool:
    """Prompt for batch ID and note, then create new batch.

    Returns:
        True if batch was created, False if cancelled
    """
    try:
        batch_id = input(_("Batch ID: ")).strip()
        if not batch_id:
            return False

        note = input(_("Note (optional): ")).strip()
    except (KeyboardInterrupt, EOFError):
        return False

    from ..commands.new import command_new_batch
    command_new_batch(batch_name=batch_id, note=note if note else None)
    print(_("\nBatch '{name}' created.").format(name=batch_id))
    return True


def _batch_edit() -> None:
    """Prompt to select batch and edit its note."""
    batch_name = _prompt_select_batch(purpose=_("edit"), skip_if_single=True)
    if not batch_name:
        return

    try:
        note = input(_("New note: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return

    from ..commands.annotate import command_annotate_batch
    command_annotate_batch(batch_name, note)
    print(_("\nBatch '{name}' note updated.").format(name=batch_name))


def _batch_drop() -> None:
    """Prompt to select batch and drop it."""
    batch_name = _prompt_select_batch(purpose=_("drop"), skip_if_single=True)
    if not batch_name:
        return

    from ..commands.drop import command_drop_batch
    command_drop_batch(batch_name)
    print(_("\nBatch '{name}' dropped.").format(name=batch_name))


def _batch_apply() -> None:
    """Prompt to select batch and apply it."""
    batch_name = _prompt_select_batch(purpose=_("apply"), skip_if_single=False)
    if not batch_name:
        return

    from ..commands.apply_from import command_apply_from_batch
    command_apply_from_batch(batch_name)
    print(_("\nBatch '{name}' applied to staging area.").format(name=batch_name))


def _prompt_select_batch(purpose: str, skip_if_single: bool = False) -> str:
    """Show list of batches and prompt user to select one.

    Args:
        purpose: String describing purpose (e.g., "edit", "drop")
        skip_if_single: If True and only one batch exists, return it
                       without prompting

    Returns:
        Selected batch name, or empty string if cancelled
    """

    batch_names = batch_query.list_batch_names()
    if not batch_names:
        print()
        print(_("No batches found."), file=sys.stderr)
        return ""

    # If only one batch and skip_if_single is True, return it directly
    if len(batch_names) == 1 and skip_if_single:
        return batch_names[0]

    print()
    print(_("Select batch to {purpose}:").format(purpose=purpose))
    for idx, name in enumerate(batch_names, 1):
        metadata = batch_query.read_batch_metadata(name)
        note = metadata.get("note", "")
        note_display = f" - {note}" if note else ""
        print(f"  [{idx}] {name}{note_display}")

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return ""

    # Parse choice - accept either number or full batch name
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(batch_names):
            return batch_names[idx]
    elif choice in batch_names:
        return choice

    print(_("\nInvalid selection."), file=sys.stderr)
    return ""


def _handle_from(flow_state: FlowState) -> None:
    """Handle [<]from action to set source."""

    use_color = Colors.enabled()
    batches = batch_query.list_batch_names()

    print()
    print(_("Pull changes from:"))
    print()

    # Build menu
    options = []
    selected_marker = _(" (selected)")

    # Option 1: Working tree
    is_selected = flow_state.source.role is LocationRole.WORKING_TREE
    marker = selected_marker if is_selected else ""
    text = _("Working tree{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  [1] {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  [1] {text}")
    options.append(("working tree", FlowLocation.WORKING_TREE))

    # Options 2+: Batches
    for idx, name in enumerate(batches, 2):
        metadata = batch_query.read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = flow_state.source.role is LocationRole.BATCH and flow_state.source.batch_name == name
        marker = selected_marker if is_selected else ""
        note_display = f" - {note}" if note else ""
        text = _("batch: {name}{note}{marker}").format(
            name=name,
            note=note_display,
            marker=marker
        )
        if use_color and is_selected:
            print(f"  [{idx}] {Colors.BOLD}{text}{Colors.RESET}")
        else:
            print(f"  [{idx}] {text}")
        options.append((name, FlowLocation.for_batch(name)))

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return  # No change if cancelled

    # Parse choice
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(options):
            flow_state.source = options[idx][1]

            # Prevent batch-to-batch state: if we just set source to a batch
            # and target is also a batch, reset target to staging
            if (flow_state.source.role is LocationRole.BATCH and
                flow_state.target.role is LocationRole.BATCH):
                flow_state.target = FlowLocation.STAGING_AREA


def _handle_to(flow_state: FlowState) -> None:
    """Handle [>]to action to set target."""

    use_color = Colors.enabled()
    batches = batch_query.list_batch_names()

    print()
    print(_("Push changes to:"))
    print()

    # Build menu
    options = []
    selected_marker = _(" (selected)")

    # Option 1: Staging
    is_selected = flow_state.target.role is LocationRole.STAGING_AREA
    marker = selected_marker if is_selected else ""
    text = _("Staging for commit{marker}").format(marker=marker)
    if use_color and is_selected:
        print(f"  [1] {Colors.BOLD}{text}{Colors.RESET}")
    else:
        print(f"  [1] {text}")
    options.append(("staging", FlowLocation.STAGING_AREA))

    # Options 2+: Existing batches
    for idx, name in enumerate(batches, 2):
        metadata = batch_query.read_batch_metadata(name)
        note = metadata.get("note", "")
        is_selected = flow_state.target.role is LocationRole.BATCH and flow_state.target.batch_name == name
        marker = selected_marker if is_selected else ""
        note_display = f" - {note}" if note else ""
        text = _("batch: {name}{note}{marker}").format(
            name=name,
            note=note_display,
            marker=marker
        )
        if use_color and is_selected:
            print(f"  [{idx}] {Colors.BOLD}{text}{Colors.RESET}")
        else:
            print(f"  [{idx}] {text}")
        options.append((name, FlowLocation.for_batch(name)))

    # Last option: Create new batch
    new_batch_idx = len(batches) + 2
    print(f"  [{new_batch_idx}] {_('New Batch...')}")
    options.append(("new", None))  # Placeholder

    print()
    try:
        choice = input(_("Select: ")).strip()
    except (KeyboardInterrupt, EOFError):
        return  # No change if cancelled

    # Parse choice
    if choice.isdigit():
        idx = int(choice) - 1
        if idx == len(options) - 1:
            # New batch
            try:
                batch_id = input(_("Batch ID: ")).strip()
                if not batch_id:
                    return
                note = input(_("Note (optional): ")).strip()
            except (KeyboardInterrupt, EOFError):
                return

            from ..commands.new import command_new_batch
            command_new_batch(batch_name=batch_id, note=note if note else None)
            flow_state.target = FlowLocation.for_batch(batch_id)
        elif 0 <= idx < len(options) - 1:
            flow_state.target = options[idx][1]

        # Prevent batch-to-batch state: if we just set target to a batch
        # and source is also a batch, reset source to working tree
        if (flow_state.target.role is LocationRole.BATCH and
            flow_state.source.role is LocationRole.BATCH):
            flow_state.source = FlowLocation.WORKING_TREE


def _handle_help(flow_state: FlowState) -> None:
    """Handle help action."""
    print_help()
    raise BypassRefresh()


def _handle_cli_command(action: str) -> None:
    """Handle arbitrary CLI command as escape hatch."""
    try:
        from ..cli.argument_parser import parse_command_line
        from ..cli.dispatch import dispatch_args

        args_list = shlex.split(action)
        args = parse_command_line(args_list, quiet=False)

        if args is not None:
            dispatch_args(args)
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
    "x": ActionHandler(needs_hunk=True, handler=_handle_fixup),
    "a": ActionHandler(needs_hunk=False, handler=_handle_again),
    "u": ActionHandler(needs_hunk=False, handler=_handle_undo),
    "U": ActionHandler(needs_hunk=False, handler=_handle_redo),
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
            _handle_from(flow_state)
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
            _handle_to(flow_state)
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
        target=FlowLocation.STAGING_AREA
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


def handle_file_selection(flow_state: FlowState) -> None:
    """
    Handle file operations submenu with flow awareness.

    Prompts user to include, skip, or discard all hunks in the selected file.
    Returns after operation or on cancel (Ctrl-C).
    """
    from ..commands.include import command_include_file
    from ..commands.skip import command_skip_file
    from ..commands.discard import command_discard_file

    use_color = Colors.enabled()

    # Get selected file from state
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    filename = line_changes.path

    # Determine available actions based on source
    if flow_state.source.role is LocationRole.BATCH:
        # When pulling from batch, skip doesn't make sense
        available_actions = ["include", "discard"]
        action_prompt = _("Action for all hunks in {filename} - [i]nclude, [d]iscard? ")
    else:
        available_actions = ["include", "skip", "discard"]
        action_prompt = _("Action for all hunks in {filename} - [i]nclude, [s]kip, [d]iscard? ")

    # Prompt for action
    print()
    try:
        if use_color:
            if "s" in available_actions:
                prompt_text = _("Action for all hunks in {filename} - {include}, {skip}, {discard}? ").format(
                    filename=f"{Colors.BOLD}{filename}{Colors.RESET}",
                    include=format_hotkey('include', 'i', Colors.GREEN),
                    skip=format_hotkey('skip', 's', ''),
                    discard=format_hotkey('discard', 'd', Colors.RED)
                )
            else:
                prompt_text = _("Action for all hunks in {filename} - {include}, {discard}? ").format(
                    filename=f"{Colors.BOLD}{filename}{Colors.RESET}",
                    include=format_hotkey('include', 'i', Colors.GREEN),
                    discard=format_hotkey('discard', 'd', Colors.RED)
                )
            action_input = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
        else:
            action_input = input(action_prompt.format(filename=filename)).strip().lower()
    except (KeyboardInterrupt, EOFError):
        # Canceled, return to main loop
        return

    # Execute based on source and target
    if action_input in ("i", "include"):
        if flow_state.source.role is LocationRole.WORKING_TREE:
            if flow_state.target.role is LocationRole.STAGING_AREA:
                command_include_file(file="", auto_advance=True)
            elif flow_state.target.role is LocationRole.BATCH:
                # Include file to batch
                from ..commands.include import command_include_to_batch
                command_include_to_batch(
                    flow_state.target.batch_name,
                    file="",
                    quiet=True,
                    auto_advance=True,
                )
            else:
                raise ValueError(f"Unknown target role: {flow_state.target.role}")
        elif flow_state.source.role is LocationRole.BATCH:
            if flow_state.target.role is not LocationRole.STAGING_AREA:
                print(_("Batch-to-batch transfers not yet supported."), file=sys.stderr)
                return
            from ..commands.include_from import command_include_from_batch
            command_include_from_batch(flow_state.source.batch_name, file="")
        else:
            raise ValueError(f"Unknown source role: {flow_state.source.role}")
        fetch_next_change()
    elif action_input in ("s", "skip"):
        if flow_state.source.role is LocationRole.BATCH:
            print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
            return
        if flow_state.target.role is LocationRole.STAGING_AREA:
            command_skip_file(auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
            from ..commands.include import command_include_to_batch
            command_include_to_batch(
                flow_state.target.batch_name,
                file="",
                auto_advance=True,
            )
        else:
            raise ValueError(f"Unknown target role: {flow_state.target.role}")
        fetch_next_change()
    elif action_input in ("d", "discard"):
        if flow_state.source.role is LocationRole.WORKING_TREE:
            if flow_state.target.role is LocationRole.STAGING_AREA:
                if confirm_destructive_operation("discard", _("This will remove all hunks from {filename} in your working tree.").format(filename=filename)):
                    command_discard_file(file="", auto_advance=True)
            elif flow_state.target.role is LocationRole.BATCH:
                # Discard file to batch
                from ..commands.discard import command_discard_to_batch
                command_discard_to_batch(
                    flow_state.target.batch_name,
                    file="",
                    quiet=True,
                    auto_advance=True,
                )
            else:
                raise ValueError(f"Unknown target role: {flow_state.target.role}")
        elif flow_state.source.role is LocationRole.BATCH:
            if flow_state.target.role is not LocationRole.STAGING_AREA:
                print(_("Batch-to-batch transfers not yet supported."), file=sys.stderr)
                return
            from ..commands.discard_from import command_discard_from_batch
            command_discard_from_batch(flow_state.source.batch_name, file="")
        else:
            raise ValueError(f"Unknown source role: {flow_state.source.role}")
        fetch_next_change()
    else:
        print(_("\nUnknown action: '{action}'").format(action=action_input))


def handle_line_selection(flow_state: FlowState) -> None:
    """
    Handle line selection submenu with flow awareness.

    Prompts user for action and line IDs, then executes the operation.
    Returns after operation or on cancel (Ctrl-C).
    """
    from ..commands.include import command_include_line
    from ..commands.skip import command_skip_line
    from ..commands.discard import command_discard_line

    use_color = Colors.enabled()

    # Get selected lines to show available line IDs
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    # Collect changed line IDs
    changed_ids = [line.id for line in line_changes.lines if line.id is not None]
    if not changed_ids:
        print(_("\nNo changed lines in this hunk."))
        return

    # Show available line IDs as ranges
    ids_text = format_id_range(changed_ids)
    ids_display = _("\nChanged line IDs: {ids}").format(ids=ids_text)
    if use_color:
        print(f"{Colors.CYAN}{ids_display}{Colors.RESET}")
    else:
        print(ids_display)

    # Determine available actions based on source
    if flow_state.source.role is LocationRole.BATCH:
        # When pulling from batch, skip doesn't make sense
        available_actions = ["include", "discard"]
        action_prompt = _("Action for lines [i]nclude, [d]iscard? ")
    else:
        available_actions = ["include", "skip", "discard"]
        action_prompt = _("Action for lines [i]nclude, [s]kip, [d]iscard? ")

    # Prompt for action
    print()
    try:
        if use_color and "s" in available_actions:
            prompt_text = _("Action for lines {include}, {skip}, {discard}? ").format(
                include=format_hotkey('include', 'i', Colors.GREEN),
                skip=format_hotkey('skip', 's', ''),
                discard=format_hotkey('discard', 'd', Colors.RED)
            )
            action_input = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
        else:
            action_input = input(action_prompt).strip().lower()
    except (KeyboardInterrupt, EOFError):
        # Canceled, return to main loop
        return

    # Normalize action
    action_map = {"i": "include", "include": "include", "s": "skip", "skip": "skip", "d": "discard", "discard": "discard"}
    action = action_map.get(action_input)

    if action is None:
        print(_("\nUnknown action: '{action}'").format(action=action_input))
        return

    # Check if action is available
    if action not in available_actions:
        print(_("\nSkip is not available when pulling from a batch."), file=sys.stderr)
        return

    # Prompt for line IDs
    line_ids = prompt_line_ids()
    if not line_ids:
        # Canceled or empty, return to main loop
        return

    # Execute operation based on source and target
    try:
        if action == "include":
            if flow_state.source.role is LocationRole.WORKING_TREE:
                if flow_state.target.role is LocationRole.STAGING_AREA:
                    command_include_line(line_ids, auto_advance=True)
                elif flow_state.target.role is LocationRole.BATCH:
                    # Include lines to batch (via skip-to-batch with line IDs)
                    from ..commands.include import command_include_to_batch
                    command_include_to_batch(
                        flow_state.target.batch_name,
                        line_ids=line_ids,
                        quiet=True,
                        auto_advance=True,
                    )
                else:
                    raise ValueError(f"Unknown target role: {flow_state.target.role}")
            elif flow_state.source.role is LocationRole.BATCH:
                if flow_state.target.role is not LocationRole.STAGING_AREA:
                    print(_("Batch-to-batch transfers not yet supported."), file=sys.stderr)
                    return
                from ..commands.include_from import command_include_from_batch
                command_include_from_batch(flow_state.source.batch_name, line_ids=line_ids)
            else:
                raise ValueError(f"Unknown source role: {flow_state.source.role}")
        elif action == "skip":
            if flow_state.source.role is LocationRole.BATCH:
                print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
                return
            if flow_state.target.role is LocationRole.STAGING_AREA:
                command_skip_line(line_ids, auto_advance=True)
            elif flow_state.target.role is LocationRole.BATCH:
                from ..commands.include import command_include_to_batch
                command_include_to_batch(
                    flow_state.target.batch_name,
                    line_ids=line_ids,
                    auto_advance=True,
                )
            else:
                raise ValueError(f"Unknown target role: {flow_state.target.role}")
        elif action == "discard":
            if flow_state.source.role is LocationRole.WORKING_TREE:
                if flow_state.target.role is LocationRole.STAGING_AREA:
                    if confirm_destructive_operation("discard", _("This will remove lines {line_ids} from your working tree.").format(line_ids=line_ids)):
                        command_discard_line(line_ids, auto_advance=True)
                elif flow_state.target.role is LocationRole.BATCH:
                    # Discard lines to batch
                    from ..commands.discard import command_discard_to_batch
                    command_discard_to_batch(
                        flow_state.target.batch_name,
                        line_ids=line_ids,
                        quiet=True,
                        auto_advance=True,
                    )
                else:
                    raise ValueError(f"Unknown target role: {flow_state.target.role}")
            elif flow_state.source.role is LocationRole.BATCH:
                if flow_state.target.role is not LocationRole.STAGING_AREA:
                    print(_("Batch-to-batch transfers not yet supported."), file=sys.stderr)
                    return
                from ..commands.discard_from import command_discard_from_batch
                command_discard_from_batch(flow_state.source.batch_name, line_ids=line_ids)
            else:
                raise ValueError(f"Unknown source role: {flow_state.source.role}")
    except Exception as e:
        print(_("\nError: {error}").format(error=e))


def handle_fixup_selection() -> None:
    """
    Handle suggest-fixup submenu for iterative candidate selection.

    Displays fixup candidates one at a time, prompting user to accept,
    move to next, reset, or cancel. Maintains iteration state across
    invocations within the same hunk context.
    """
    from ..commands.suggest_fixup import command_suggest_fixup, _load_suggest_fixup_state, _reset_suggest_fixup_state

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
            state = _load_suggest_fixup_state()
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
            _reset_suggest_fixup_state()
            print(_("\nCanceled."))
            break
        else:
            print(_("\nUnknown action: '{action}'").format(action=action))


def handle_quit() -> None:
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
    print(_("  a, again     - Clear state and start fresh pass through skipped hunks"))
    print(_("  u, undo      - Undo the most recent operation"))
    print(_("  U, redo      - Redo the most recently undone operation"))
    print(_("  l, lines     - Select specific lines from this hunk"))
    print(_("  f, file      - Include or skip all hunks in this file"))
    print(_("  x, fixup     - Suggest which commit to fixup (iterative)"))
    print(_("  !<cmd>       - Run shell command (e.g., !git log, or just ! to prompt)"))
    print(_("  ?, help      - Show this help message"))
    print()
