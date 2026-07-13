"""Whole-file action menu for interactive mode."""

from __future__ import annotations

import sys

from ..commands.discard import command_discard_file, command_discard_to_batch
from ..commands.discard_from import command_discard_from_batch
from ..commands.include import command_include_file, command_include_to_batch
from ..commands.include_from import command_include_from_batch
from ..commands.skip import command_skip_file
from ..data.line_state import load_line_changes_from_state
from ..i18n import _
from ..output.colors import Colors, format_hotkey
from .flow import FlowState, LocationRole
from .prompts import confirm_destructive_operation, wrap_prompt_for_readline


def handle_file_selection_menu(flow_state: FlowState) -> None:
    """Handle file operations submenu with flow awareness."""
    use_color = Colors.enabled()

    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    filename = line_changes.path

    if flow_state.source.role is LocationRole.BATCH:
        available_actions = ["include", "discard"]
        action_prompt = _(
            "Action for all hunks in {filename} - [i]nclude, [d]iscard? "
        )
    else:
        available_actions = ["include", "skip", "discard"]
        action_prompt = _(
            "Action for all hunks in {filename} - [i]nclude, [s]kip, [d]iscard? "
        )

    print()
    try:
        if use_color:
            if "s" in available_actions:
                prompt_text = _(
                    "Action for all hunks in {filename} - "
                    "{include}, {skip}, {discard}? "
                ).format(
                    filename=f"{Colors.BOLD}{filename}{Colors.RESET}",
                    include=format_hotkey("include", "i", Colors.GREEN),
                    skip=format_hotkey("skip", "s", ""),
                    discard=format_hotkey("discard", "d", Colors.RED),
                )
            else:
                prompt_text = _(
                    "Action for all hunks in {filename} - {include}, {discard}? "
                ).format(
                    filename=f"{Colors.BOLD}{filename}{Colors.RESET}",
                    include=format_hotkey("include", "i", Colors.GREEN),
                    discard=format_hotkey("discard", "d", Colors.RED),
            )
            action_input = input(wrap_prompt_for_readline(prompt_text)).strip().lower()
        else:
            action_input = (
                input(action_prompt.format(filename=filename)).strip().lower()
            )
    except (KeyboardInterrupt, EOFError):
        return

    if action_input in ("i", "include"):
        _handle_file_include(flow_state)
    elif action_input in ("s", "skip"):
        _handle_file_skip(flow_state)
    elif action_input in ("d", "discard"):
        _handle_file_discard(flow_state, filename)
    else:
        print(_("\nUnknown action: '{action}'").format(action=action_input))


def _handle_file_include(flow_state: FlowState) -> None:
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            command_include_file(file="", auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
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
        command_include_from_batch(flow_state.source.batch_name, file="")
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")


def _handle_file_skip(flow_state: FlowState) -> None:
    if flow_state.source.role is LocationRole.BATCH:
        print(_("Skip is not available when pulling from a batch."), file=sys.stderr)
        return
    if flow_state.target.role not in (
        LocationRole.STAGING_AREA,
        LocationRole.BATCH,
    ):
        raise ValueError(f"Unknown target role: {flow_state.target.role}")
    command_skip_file(auto_advance=True)


def _handle_file_discard(flow_state: FlowState, filename: str) -> None:
    if flow_state.source.role is LocationRole.WORKING_TREE:
        if flow_state.target.role is LocationRole.STAGING_AREA:
            warning = _(
                "This will remove all hunks from {filename} in your working tree."
            ).format(filename=filename)
            if confirm_destructive_operation("discard", warning):
                command_discard_file(file="", auto_advance=True)
        elif flow_state.target.role is LocationRole.BATCH:
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
        command_discard_from_batch(flow_state.source.batch_name, file="")
    else:
        raise ValueError(f"Unknown source role: {flow_state.source.role}")
