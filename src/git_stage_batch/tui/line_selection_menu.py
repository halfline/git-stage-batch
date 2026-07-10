"""Line action menu for interactive mode."""

from __future__ import annotations

import sys

from ..commands.discard import command_discard_line, command_discard_to_batch
from ..commands.discard_from import command_discard_from_batch
from ..commands.include import command_include_line, command_include_to_batch
from ..commands.include_from import command_include_from_batch
from ..commands.skip import command_skip_line
from ..data.line_state import load_line_changes_from_state
from ..data.progress import format_id_range
from ..i18n import _
from ..output.colors import Colors, format_hotkey
from .flow import FlowState, LocationRole
from .prompts import (
    confirm_destructive_operation,
    prompt_line_ids,
    wrap_prompt_for_readline,
)


def handle_line_selection_menu(flow_state: FlowState) -> None:
    """Handle line selection submenu with flow awareness."""
    use_color = Colors.enabled()

    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    changed_ids = [line.id for line in line_changes.lines if line.id is not None]
    if not changed_ids:
        print(_("\nNo changed lines in this hunk."))
        return

    ids_text = format_id_range(changed_ids)
    ids_display = _("\nChanged line IDs: {ids}").format(ids=ids_text)
    if use_color:
        print(f"{Colors.CYAN}{ids_display}{Colors.RESET}")
    else:
        print(ids_display)

    if flow_state.source.role is LocationRole.BATCH:
        available_actions = ["include", "discard"]
        action_prompt = _("Action for lines [i]nclude, [d]iscard? ")
    else:
        available_actions = ["include", "skip", "discard"]
        action_prompt = _("Action for lines [i]nclude, [s]kip, [d]iscard? ")

    print()
    try:
        if use_color and "s" in available_actions:
            prompt_text = _(
                "Action for lines {include}, {skip}, {discard}? "
            ).format(
                include=format_hotkey("include", "i", Colors.GREEN),
                skip=format_hotkey("skip", "s", ""),
                discard=format_hotkey("discard", "d", Colors.RED),
            )
            action_input = (
                input(wrap_prompt_for_readline(prompt_text)).strip().lower()
            )
        else:
            action_input = input(action_prompt).strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    action_map = {
        "i": "include",
        "include": "include",
        "s": "skip",
        "skip": "skip",
        "d": "discard",
        "discard": "discard",
    }
    action = action_map.get(action_input)

    if action is None:
        print(_("\nUnknown action: '{action}'").format(action=action_input))
        return

    if action not in available_actions:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return

    line_ids = prompt_line_ids()
    if not line_ids:
        return

    try:
        if action == "include":
            _handle_line_include(flow_state, line_ids)
        elif action == "skip":
            _handle_line_skip(flow_state, line_ids)
        elif action == "discard":
            _handle_line_discard(flow_state, line_ids)
    except Exception as error:
        print(_("\nError: {error}").format(error=error))


def _handle_line_include(flow_state: FlowState, line_ids: str) -> None:
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            command_include_line(line_ids, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
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
        command_include_from_batch(flow_state.source.batch_name, line_ids=line_ids)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def _handle_line_skip(flow_state: FlowState, line_ids: str) -> None:
    if flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return
    if flow_state.target.role is LocationRole.STAGING_AREA:
        command_skip_line(line_ids, auto_advance=True)
    elif flow_state.target.role is LocationRole.BATCH:
        command_include_to_batch(
            flow_state.target.batch_name,
            line_ids=line_ids,
            auto_advance=True,
        )
    else:
        raise ValueError(f"Unknown target role: {flow_state.target.role}")


def _handle_line_discard(flow_state: FlowState, line_ids: str) -> None:
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            warning = _(
                "This will remove lines {line_ids} from your working tree."
            ).format(line_ids=line_ids)
            if confirm_destructive_operation("discard", warning):
                command_discard_line(line_ids, auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
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
        command_discard_from_batch(flow_state.source.batch_name, line_ids=line_ids)
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")
