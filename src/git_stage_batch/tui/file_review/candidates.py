"""Candidate browser for batch-backed file review."""

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
from ..flow import LocationRole
from ..prompts import unlocked_input, wrap_prompt_for_readline


def browse_candidates(state: FileReviewSessionState) -> None:
    """Preview or execute candidate operations for a reviewed batch file."""
    if state.flow_state.source.role is not LocationRole.BATCH:
        print(
            _("Candidate browsing is only available when pulling from a batch."),
            file=sys.stderr,
        )
        return

    operation = _prompt_candidate_operation()
    if operation is None:
        return

    batch_name = state.flow_state.source.require_batch_name()
    selector = f"{batch_name}:{operation}"

    try:
        from ...commands.show_from import command_show_from_batch

        command_show_from_batch(selector, file=state.file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)
        return

    while True:
        choice = _prompt_candidate_action()
        if choice is None:
            return

        if choice.isdigit():
            _preview_candidate(batch_name, operation, int(choice), state.file_path)
            continue
        if choice.startswith("e "):
            ordinal_text = choice[2:].strip()
            if not ordinal_text.isdigit():
                print(_("Invalid candidate selection."), file=sys.stderr)
                continue
            _execute_candidate(batch_name, operation, int(ordinal_text), state.file_path)
            return

        print(_("Invalid candidate selection."), file=sys.stderr)


def _prompt_candidate_operation() -> str | None:
    options = (
        (_("include"), "i"),
        (_("apply"), "a"),
        (_("quit"), "q"),
    )
    try:
        choice = unlocked_input(
            wrap_prompt_for_readline(
                _("Candidate operation {include}, {apply}, or {quit}: ").format(
                    include=format_hotkey(options[0][0], "i"),
                    apply=format_hotkey(options[1][0], "a"),
                    quit=format_hotkey(options[2][0], "q"),
                )
            )
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None

    normalized = normalize_localized_choice(
        choice,
        stable_codes=frozenset({"i", "a", "q"}),
        legacy_words={
            "include": "i",
            "apply": "a",
            "quit": "q",
            "cancel": "q",
        },
        localized_words=localized_word_aliases(
            (str(word), action) for word, action in options
        ),
    )
    if normalized == "q":
        return None
    if normalized == "i":
        return "include"
    if normalized == "a":
        return "apply"

    print(_("Invalid candidate operation."), file=sys.stderr)
    return None


def _prompt_candidate_action() -> str | None:
    try:
        choice = unlocked_input(
            wrap_prompt_for_readline(
                _("Candidate number to preview, e N to execute, or q: ")
            )
        ).strip()
    except (KeyboardInterrupt, EOFError):
        return None

    normalized = normalize_localized_choice(
        choice,
        stable_codes=frozenset({"q"}),
        legacy_words={"quit": "q", "back": "q"},
        localized_words=localized_word_aliases(
            ((str(_("quit")), "q"), (str(_("back")), "q"))
        ),
    )
    if normalized == "q":
        return None
    return normalized


def _preview_candidate(
    batch_name: str,
    operation: str,
    ordinal: int,
    file_path: str,
) -> None:
    from ...commands.show_from import command_show_from_batch

    try:
        command_show_from_batch(
            f"{batch_name}:{operation}:{ordinal}",
            file=file_path,
        )
    except CommandError as e:
        print(e.message, file=sys.stderr)


def _execute_candidate(
    batch_name: str,
    operation: str,
    ordinal: int,
    file_path: str,
) -> None:
    selector = f"{batch_name}:{operation}:{ordinal}"
    try:
        if operation == "include":
            from ...commands.include_from import command_include_from_batch

            command_include_from_batch(selector, file=file_path)
            return

        from ...commands.apply_from import command_apply_from_batch

        command_apply_from_batch(selector, file=file_path)
    except CommandError as e:
        print(e.message, file=sys.stderr)
