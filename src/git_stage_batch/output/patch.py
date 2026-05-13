"""Colored patch printing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .colors import Colors

if TYPE_CHECKING:
    from ..core.models import BinaryFileChange, GitlinkChange


def print_colored_patch(patch_text: str) -> None:
    """Print a patch with colored diff lines."""
    use_color = Colors.enabled()

    for line in patch_text.splitlines(keepends=True):
        if use_color:
            if line.startswith('+++') or line.startswith('---'):
                print(f"{Colors.BOLD}{line}{Colors.RESET}", end="")
            elif line.startswith('@@'):
                print(f"{Colors.CYAN}{line}{Colors.RESET}", end="")
            elif line.startswith('+'):
                print(f"{Colors.GREEN}{line}{Colors.RESET}", end="")
            elif line.startswith('-'):
                print(f"{Colors.RED}{line}{Colors.RESET}", end="")
            else:
                print(line, end="")
        else:
            print(line, end="")


def print_binary_file_change(binary_change: BinaryFileChange) -> None:
    """Print a binary file change with colored output.

    Binary files are displayed as atomic units with their file path and change type.
    """
    use_color = Colors.enabled()

    # Determine file path to display
    if binary_change.is_new_file():
        path = binary_change.new_path
        change_desc = "added"
        color = Colors.GREEN if use_color else ""
    elif binary_change.is_deleted_file():
        path = binary_change.old_path
        change_desc = "deleted"
        color = Colors.RED if use_color else ""
    else:
        path = binary_change.new_path
        change_desc = "modified"
        color = Colors.YELLOW if use_color else ""

    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""

    # Print file header
    print(f"{bold}{path}{reset} :: {color}Binary file {change_desc}{reset}")


def print_gitlink_change(gitlink_change: GitlinkChange) -> None:
    """Print a gitlink/submodule pointer change as an atomic unit."""
    use_color = Colors.enabled()

    path = gitlink_change.path()
    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""

    if gitlink_change.is_new_file():
        color = Colors.GREEN if use_color else ""
        print(f"{bold}{path}{reset} :: {color}Submodule added at {_short_oid(gitlink_change.new_oid)}{reset}")
        return

    if gitlink_change.is_deleted_file():
        color = Colors.RED if use_color else ""
        print(f"{bold}{path}{reset} :: {color}Submodule removed from {_short_oid(gitlink_change.old_oid)}{reset}")
        return

    color = Colors.YELLOW if use_color else ""
    print(f"{bold}{path}{reset} :: {color}Submodule pointer modified{reset}")
    print(f"old {_short_oid(gitlink_change.old_oid)}")
    print(f"new {_short_oid(gitlink_change.new_oid)}")


def _short_oid(oid: str | None) -> str:
    """Return a compact object id for display."""
    return oid[:12] if oid else "unknown"
