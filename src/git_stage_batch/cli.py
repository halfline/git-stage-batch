"""CLI entry point for git-stage-batch."""

from __future__ import annotations

import argparse
import sys

from .commands import (
    command_again,
    command_discard,
    command_discard_line,
    command_exclude,
    command_exclude_line,
    command_include,
    command_include_line,
    command_show,
    command_start,
    command_status,
    command_stop,
)


def main() -> None:
    """Main CLI entry point."""
    argument_parser = argparse.ArgumentParser(prog="git-stage-batch", add_help=False)
    argument_parser.add_argument("command", nargs="?", default="")
    argument_parser.add_argument("argument", nargs="?", default="")
    parsed_arguments = argument_parser.parse_args()

    command = parsed_arguments.command
    argument = parsed_arguments.argument

    if command in ("", "-h", "--help", "help"):
        print(
            "Usage: git-stage-batch {start|show|include|exclude|discard|"
            "include-line IDS|exclude-line IDS|discard-line IDS|again|stop|status}"
        )
        sys.exit(0)

    dispatch_table = {
        "start":         lambda: command_start(),
        "show":          lambda: command_show(),
        "include":       lambda: command_include(),
        "exclude":       lambda: command_exclude(),
        "discard":       lambda: command_discard(),
        "include-line":  lambda: command_include_line(argument),
        "exclude-line":  lambda: command_exclude_line(argument),
        "discard-line":  lambda: command_discard_line(argument),
        "again":         lambda: command_again(),
        "stop":          lambda: command_stop(),
        "status":        lambda: command_status(),
    }

    if command not in dispatch_table:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)

    dispatch_table[command]()


if __name__ == "__main__":
    main()
