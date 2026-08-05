"""Block action command execution for file review."""

from __future__ import annotations

import sys

from ...exceptions import CommandError
from ...i18n import _
from ...output.colors import format_hotkey
from .session import FileReviewSessionState
from ..action_prompt_choices import (
    localized_word_aliases,
    normalize_localized_choice,
)
from ..prompts import (
    confirm_destructive_operation,
    unlocked_input,
    wrap_prompt_for_readline,
)


def apply_block_action(state: FileReviewSessionState, action: str) -> None:
    """Run a block or unblock action for the reviewed file."""
    if action == "B":
        if not confirm_destructive_operation(
            "block",
            _("This will add the reviewed file to ignore state."),
        ):
            return

        local_only = prompt_block_local_only()
        if local_only is None:
            return

        try:
            block_review_file(state.file_path, local_only=local_only)
        except CommandError as e:
            print(e.message, file=sys.stderr)
        return

    try:
        unblock_review_file(state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)


def prompt_block_local_only() -> bool | None:
    """Prompt for the block-file destination."""
    local_label = _("local exclude")
    quit_label = _("quit")
    try:
        choice = unlocked_input(
            wrap_prompt_for_readline(
                _("Block target {gitignore}, {local}, or {quit}: ").format(
                    gitignore=format_hotkey("gitignore", "g"),
                    local=format_hotkey(local_label, "l"),
                    quit=format_hotkey(quit_label, "q"),
                )
            )
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None

    normalized = normalize_localized_choice(
        choice,
        stable_codes=frozenset({"g", "l", "q"}),
        legacy_words={
            "gitignore": "g",
            "local": "l",
            "local exclude": "l",
            "quit": "q",
            "cancel": "q",
        },
        localized_words=localized_word_aliases(
            ((str(local_label), "l"), (str(quit_label), "q"))
        ),
    )
    if normalized == "q":
        return None
    if normalized == "l":
        return True
    if normalized in {"", "g"}:
        return False

    print(_("Invalid block target."), file=sys.stderr)
    return None


def block_review_file(file_path: str, *, local_only: bool) -> None:
    """Block a reviewed file from future review."""
    from ...commands.block_file import command_block_file

    command_block_file(file_path, local_only=local_only)


def unblock_review_file(file_path: str) -> None:
    """Remove a reviewed file from ignore state."""
    from ...commands.unblock_file import command_unblock_file

    command_unblock_file(file_path)
