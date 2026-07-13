"""Action prompt choices and normalization for interactive TUI mode."""

from __future__ import annotations

from dataclasses import dataclass

from ..output.colors import Colors


ActionPromptOption = tuple[str, str, str]


@dataclass(frozen=True)
class ActionPromptOptionGroups:
    primary: tuple[ActionPromptOption, ...]
    scope: tuple[ActionPromptOption, ...]
    flow: tuple[ActionPromptOption, ...]
    more: tuple[ActionPromptOption, ...]


def action_prompt_option_groups(
    *,
    has_hunk: bool,
    use_color: bool,
) -> ActionPromptOptionGroups:
    """Return grouped action choices for the interactive action prompt."""
    if has_hunk:
        return ActionPromptOptionGroups(
            primary=(
                ("include", "i", Colors.GREEN if use_color else ""),
                ("skip", "s", ""),
                ("discard", "d", Colors.RED if use_color else ""),
                ("quit", "q", ""),
            ),
            scope=(
                ("lines", "l", ""),
                ("file", "f", ""),
                ("view", "v", ""),
            ),
            flow=(
                ("from", "<", ""),
                ("to", ">", ""),
            ),
            more=(
                ("again", "a", ""),
                ("undo", "u", ""),
                ("redo", "U", ""),
                ("status", "S", ""),
                ("assets", "A", ""),
                ("batch", "b", ""),
                ("open", "o", ""),
                ("fixup", "x", ""),
                ("cmd", "!", ""),
                ("help", "?", ""),
            ),
        )

    return ActionPromptOptionGroups(
        primary=(
            ("quit", "q", ""),
            ("help", "?", ""),
        ),
        scope=(),
        flow=(
            ("from", "<", ""),
            ("to", ">", ""),
        ),
        more=(
            ("undo", "u", ""),
            ("redo", "U", ""),
            ("status", "S", ""),
            ("assets", "A", ""),
            ("batch", "b", ""),
            ("open", "o", ""),
            ("cmd", "!", ""),
        ),
    )


def normalize_action_prompt_choice(choice: str) -> str:
    """Return the single-character action code for a prompt choice."""
    if choice in {"U", "S", "A"}:
        return choice

    choice_lower = choice.lower()
    word_to_letter = {
        "include": "i",
        "skip": "s",
        "discard": "d",
        "quit": "q",
        "again": "a",
        "undo": "u",
        "redo": "U",
        "status": "S",
        "assets": "A",
        "install-assets": "A",
        "lines": "l",
        "file": "f",
        "review": "v",
        "view": "v",
        "open": "o",
        "files": "o",
        "batch": "b",
        "fixup": "x",
        "command": "!",
        "help": "?",
        "from": "<",
        "to": ">",
    }
    return word_to_letter.get(choice_lower, choice_lower)
