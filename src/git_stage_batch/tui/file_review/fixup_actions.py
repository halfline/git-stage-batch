"""Suggest-fixup actions for file review."""

from __future__ import annotations

import sys

from ...exceptions import CommandError
from ...i18n import _
from .session import FileReviewSessionState
from ..flow import LocationRole
from ..prompts import prompt_fixup_action, prompt_line_ids


def apply_fixup_action(state: FileReviewSessionState) -> None:
    """Run the suggest-fixup flow for reviewed line IDs."""
    if state.flow_state.source.role is LocationRole.BATCH:
        print(
            _("Suggest-fixup is not available when pulling from a batch."),
            file=sys.stderr,
        )
        return

    line_ids = prompt_line_ids()
    if not line_ids:
        return

    use_color = sys.stdout.isatty()

    try:
        suggest_fixup_for_lines(line_ids, file_path=state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)
        return

    while True:
        print()
        action = prompt_fixup_action(use_color=use_color)

        if action == "y":
            commit_hash = read_last_fixup_commit_hash()
            if commit_hash is not None:
                print()
                print(_("Create fixup commit with:"))
                print(f"  git commit --fixup={commit_hash}")
                print()
            return
        if action == "n":
            try:
                suggest_fixup_for_lines(line_ids, file_path=state.file_path)
            except CommandError as e:
                print(e.message, file=sys.stderr)
                return
            continue
        if action == "r":
            try:
                suggest_fixup_for_lines(
                    line_ids,
                    file_path=state.file_path,
                    reset=True,
                )
            except CommandError as e:
                print(e.message, file=sys.stderr)
                return
            continue
        if action == "q":
            clear_file_review_fixup_state()
            print(_("\nCanceled."))
            return

        print(_("Unknown action: {action}").format(action=action))


def suggest_fixup_for_lines(
    line_ids: str,
    *,
    file_path: str,
    reset: bool = False,
) -> None:
    """Show a suggest-fixup candidate for reviewed line IDs."""
    from ...commands.suggest_fixup import command_suggest_fixup_line

    if reset:
        command_suggest_fixup_line(line_ids, file=file_path, reset=True)
        return

    command_suggest_fixup_line(line_ids, file=file_path)


def read_last_fixup_commit_hash() -> str | None:
    """Return the last shown fixup commit hash for review display."""
    from ...data.suggest_fixup_state import read_suggest_fixup_state

    fixup_state = read_suggest_fixup_state()
    if fixup_state and fixup_state.get("last_shown_commit"):
        return fixup_state["last_shown_commit"][:7]
    return None


def clear_file_review_fixup_state() -> None:
    """Clear persisted suggest-fixup selection state."""
    from ...data.suggest_fixup_state import clear_suggest_fixup_state

    clear_suggest_fixup_state()
