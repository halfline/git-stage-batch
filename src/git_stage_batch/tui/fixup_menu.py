"""Suggest-fixup submenu for interactive mode."""

from __future__ import annotations

from ..commands.suggest_fixup import command_suggest_fixup
from ..data.line_state import load_line_changes_from_state
from ..data.suggest_fixup_state import (
    clear_suggest_fixup_state,
    read_suggest_fixup_state,
)
from ..exceptions import CommandError
from ..i18n import _
from ..output.colors import Colors
from .prompts import prompt_fixup_action


def handle_fixup_menu() -> None:
    """
    Handle suggest-fixup submenu for iterative candidate selection.

    Displays fixup candidates one at a time, prompting user to accept,
    move to next, reset, or cancel. Maintains iteration state across
    invocations within the same hunk context.
    """
    use_color = Colors.enabled()
    line_changes = load_line_changes_from_state()
    if line_changes is None:
        return

    try:
        command_suggest_fixup()
    except CommandError as error:
        print(f"\n{error.message}")
        return

    while True:
        print()
        action = prompt_fixup_action(use_color=use_color)

        if action == "y":
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
            try:
                command_suggest_fixup()
            except CommandError as error:
                print(f"\n{error.message}")
                break
        elif action == "r":
            try:
                command_suggest_fixup(reset=True)
            except CommandError as error:
                print(f"\n{error.message}")
                break
        elif action == "q":
            clear_suggest_fixup_state()
            print(_("\nCanceled."))
            break
        else:
            print(_("\nUnknown action: '{action}'").format(action=action))
