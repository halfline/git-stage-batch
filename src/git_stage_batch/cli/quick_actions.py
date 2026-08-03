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
    """Expand a shortcut only when it occupies the subcommand position."""
    command_index = 0
    while command_index < len(args):
        argument = args[command_index]
        if argument == "-C":
            command_index += 2
            continue
        if argument.startswith("-C") and argument != "-C":
            command_index += 1
            continue
        if argument == "-i":
            command_index += 1
            continue
        break

    if command_index >= len(args):
        return list(args)
    expansion = QUICK_ACTIONS.get(args[command_index])
    if expansion is None:
        return list(args)
    return [*args[:command_index], *expansion, *args[command_index + 1:]]
