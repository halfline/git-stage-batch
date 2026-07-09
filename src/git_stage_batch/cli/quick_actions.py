"""Quick action expansion for CLI aliases."""

from __future__ import annotations


QUICK_ACTIONS = {
    "?": ["--help"],
    "if": ["include", "--file"],
    "il": ["include", "--line"],
    "sf": ["skip", "--file"],
    "sl": ["skip", "--line"],
    "df": ["discard", "--file"],
    "dl": ["discard", "--line"],
}


def expand_quick_actions(args: list[str]) -> list[str]:
    """Expand shortcut arguments into their long command forms."""
    expanded = []
    for arg in args:
        if arg in QUICK_ACTIONS:
            expanded.extend(QUICK_ACTIONS[arg])
        else:
            expanded.append(arg)
    return expanded
