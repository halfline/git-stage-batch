"""Command-line interface for git-stage-batch."""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__
from . import commands
from .display import print_colored_patch
from .i18n import _
from .state import (
    CommandError,
    exit_with_error,
    get_current_hunk_patch_file_path,
    get_state_directory_path,
    read_text_file_contents,
    require_git_repository,
)


def display_cached_hunk() -> None:
    """Display the currently cached hunk."""
    patch_file = get_current_hunk_patch_file_path()
    if patch_file.exists():
        patch_text = read_text_file_contents(patch_file)
        if patch_text.strip():
            print_colored_patch(patch_text)


class GitHelpArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that tries to use git help for --help."""

    def print_help(self, file=None):
        """Try to use git help, fall back to argparse help."""
        try:
            result = subprocess.run(
                ["git", "help", "stage-batch"],
                check=False,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                return
        except (FileNotFoundError, OSError):
            pass

        # Fall back to standard argparse help
        super().print_help(file)


def parse_command_line(args: list[str], *, quiet: bool = False) -> argparse.Namespace | None:
    """Parse command-line arguments with quick action expansion.

    Args:
        args: Command-line arguments to parse
        quiet: If True, suppress error output on parse failure

    Returns:
        Parsed arguments on success, None if parsing failed
    """
    # Mapping from shortcuts to their expanded forms
    quick_actions = {
        '?': ['--help'],
        'if': ['include', '--file'],
        'sf': ['skip', '--file'],
        'df': ['discard', '--file'],
    }

    # Expand quick actions
    expanded = []
    for arg in args:
        if arg in quick_actions:
            expanded.extend(quick_actions[arg])
        else:
            expanded.append(arg)

    # Create parser
    parser = GitHelpArgumentParser(
        prog="git-stage-batch",
        description=_("Hunk-by-hunk and line-by-line staging for git"),
        exit_on_error=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"git-stage-batch {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        help=_("Available commands"),
    )

    # start - Start a new batch staging session
    parser_start = subparsers.add_parser(
        "start",
        help=_("Start a new batch staging session"),
    )
    parser_start.add_argument(
        "-U", "--unified",
        type=int,
        default=3,
        metavar="N",
        help=_("Number of context lines in diff output (default: 3)"),
    )
    def start_cli(args):
        commands.command_start(unified=args.unified)
        # Display the cached hunk
        current_lines = commands.load_current_lines_from_state()
        if current_lines:
            commands.print_annotated_hunk_with_aligned_gutter(current_lines)

    parser_start.set_defaults(func=start_cli)

    # stop - Stop the current session and clear state
    parser_stop = subparsers.add_parser(
        "stop",
        help=_("Stop the current session and clear state"),
    )
    parser_stop.set_defaults(func=lambda _: commands.command_stop())

    # again - Clear state and start a fresh pass
    parser_again = subparsers.add_parser(
        "again",
        aliases=["a"],
        help=_("Clear state and start a fresh pass"),
    )
    parser_again.set_defaults(func=lambda _: commands.command_again())

    # show - Show the current hunk
    parser_show = subparsers.add_parser(
        "show",
        help=_("Show the current hunk"),
    )
    parser_show.set_defaults(func=lambda _: commands.command_show())

    # include - Include (stage) the current hunk or entire file
    parser_include = subparsers.add_parser(
        "include",
        aliases=["i"],
        help=_("Include (stage) the current hunk"),
    )
    parser_include.add_argument(
        "--file",
        action="store_true",
        help=_("Stage the entire file containing the current hunk"),
    )
    def include_cli(args):
        if args.file:
            commands.command_include_file()
        else:
            commands.command_include()
        display_cached_hunk()

    parser_include.set_defaults(func=include_cli)

    # skip - Skip the current hunk or entire file
    parser_skip = subparsers.add_parser(
        "skip",
        aliases=["s"],
        help=_("Skip the current hunk without staging"),
    )
    parser_skip.add_argument(
        "--file",
        action="store_true",
        help=_("Skip the entire file containing the current hunk"),
    )
    parser_skip.set_defaults(func=lambda args: (
        commands.command_skip_file() if args.file
        else commands.command_skip()
    ))

    # discard - Discard the current hunk or entire file from working tree
    parser_discard = subparsers.add_parser(
        "discard",
        aliases=["d"],
        help=_("Discard the current hunk from working tree"),
    )
    parser_discard.add_argument(
        "--file",
        action="store_true",
        help=_("Discard the entire file containing the current hunk"),
    )
    parser_discard.set_defaults(func=lambda args: (
        commands.command_discard_file() if args.file
        else commands.command_discard()
    ))

    # status - Show current session status
    parser_status = subparsers.add_parser(
        "status",
        aliases=["st"],
        help=_("Show current session status"),
    )
    parser_status.set_defaults(func=lambda _: commands.command_status())

    # abort - Abort session and restore repository state
    parser_abort = subparsers.add_parser(
        "abort",
        help=_("Abort session and undo all changes"),
    )
    parser_abort.set_defaults(func=lambda _: commands.command_abort())

    # Parse arguments, return None on failure
    try:
        return parser.parse_args(expanded)
    except argparse.ArgumentError:
        if not quiet:
            parser.print_usage(sys.stderr)
        return None


def dispatch_args(args: argparse.Namespace) -> None:
    """Execute the command based on parsed arguments.

    Args:
        args: Parsed arguments from ArgumentParser
    """
    if args.command is None:
        # No command provided - check if session is active
        require_git_repository()  # This will print error and exit if not in a git repo

        if get_state_directory_path().exists():
            # Default to include when session is active
            commands.command_include()
            display_cached_hunk()
        else:
            # No session - show helpful message
            exit_with_error(
                _("No batch staging session in progress.") + "\n" +
                _("Run 'git-stage-batch start' to begin.")
            )
    else:
        args.func(args)


def main() -> None:
    """Main entry point for git-stage-batch."""
    args = parse_command_line(sys.argv[1:], quiet=False)
    if args is not None:
        dispatch_args(args)


if __name__ == "__main__":
    try:
        main()
    except CommandError as e:
        if e.message:
            print(e.message, file=sys.stderr)
        sys.exit(e.exit_code)
