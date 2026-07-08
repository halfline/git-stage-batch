"""Action dispatch for interactive mode."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable

from ..data.file_tracking import auto_add_untracked_files
from ..data.hunk_tracking import fetch_next_change
from ..exceptions import BypassRefresh, QuitInteractive
from ..i18n import _
from ..utils.paths import get_selected_hunk_hash_file_path
from .asset_menu import handle_asset_menu
from .batch_menu import handle_batch_menu
from .cli_escape import handle_cli_escape
from .file_review import handle_current_file_review, handle_file_browser
from .file_selection_menu import handle_file_selection_menu
from .fixup_menu import handle_fixup_menu
from .flow import FlowState, LocationRole
from .flow_actions import handle_flow_action
from .help_display import print_help
from .history_actions import handle_redo, handle_undo
from .hunk_actions import (
    handle_hunk_discard,
    handle_hunk_include,
    handle_hunk_skip,
)
from .line_selection_menu import handle_line_selection_menu
from .session_quit import handle_quit
from .shell_command import handle_shell_command
from .status_action import handle_status


@dataclass
class ActionHandler:
    """Configuration for an interactive action."""

    needs_hunk: bool
    handler: Callable[[FlowState], None]


def _handle_again(flow_state: FlowState) -> None:
    """Handle again action - restart from first hunk."""
    hunk_hash_file = get_selected_hunk_hash_file_path()
    if hunk_hash_file.exists():
        hunk_hash_file.unlink()

    auto_add_untracked_files()
    fetch_next_change()


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
        print(
            _("Suggest-fixup is not available when pulling from a batch."),
            file=sys.stderr,
        )
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
    "u": ActionHandler(needs_hunk=False, handler=handle_undo),
    "U": ActionHandler(needs_hunk=False, handler=handle_redo),
    "S": ActionHandler(needs_hunk=False, handler=handle_status),
    "A": ActionHandler(needs_hunk=False, handler=_handle_assets),
    "b": ActionHandler(needs_hunk=False, handler=_handle_batch),
    "?": ActionHandler(needs_hunk=False, handler=_handle_help),
    "q": ActionHandler(needs_hunk=False, handler=_handle_quit),
}


def dispatch_action(
    action: str,
    has_hunk: bool,
    use_color: bool,
    flow_state: FlowState,
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
