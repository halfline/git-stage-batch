"""Colored patch printing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..i18n import _
from .colors import Colors

if TYPE_CHECKING:
    from ..core.models import BinaryFileChange, FileModeChange, GitlinkChange, RenameChange, TextFileDeletionChange


def print_binary_file_change(binary_change: BinaryFileChange) -> None:
    """Print a binary file change with colored output.

    Binary files are displayed as atomic units with their file path and change type.
    """
    use_color = Colors.enabled()

    # Determine file path to display
    if binary_change.is_new_file():
        path = binary_change.new_path
        change_desc = _("added")
        color = Colors.GREEN if use_color else ""
    elif binary_change.is_deleted_file():
        path = binary_change.old_path
        change_desc = _("deleted")
        color = Colors.RED if use_color else ""
    else:
        path = binary_change.new_path
        change_desc = _("modified")
        color = Colors.YELLOW if use_color else ""

    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""

    # Print file header
    description = _("Binary file {change}").format(change=change_desc)
    print(
        _("{path} :: {description}").format(
            path=f"{bold}{path}{reset}",
            description=f"{color}{description}{reset}",
        )
    )


def print_file_mode_change(mode_change: FileModeChange) -> None:
    """Print an atomic executable-mode change."""
    executable = mode_change.new_mode == "100755"
    description = (
        _("Executable bit added") if executable else _("Executable bit removed")
    )
    print(
        _("{path} :: {description}").format(
            path=mode_change.path(),
            description=description,
        )
    )


def print_gitlink_change(gitlink_change: GitlinkChange) -> None:
    """Print a gitlink/submodule pointer change as an atomic unit."""
    use_color = Colors.enabled()

    path = gitlink_change.path()
    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""

    if gitlink_change.is_new_file():
        color = Colors.GREEN if use_color else ""
        description = _("Submodule added at {oid}").format(
            oid=_short_oid(gitlink_change.new_oid)
        )
        print(
            _("{path} :: {description}").format(
                path=f"{bold}{path}{reset}",
                description=f"{color}{description}{reset}",
            )
        )
        return

    if gitlink_change.is_deleted_file():
        color = Colors.RED if use_color else ""
        description = _("Submodule removed from {oid}").format(
            oid=_short_oid(gitlink_change.old_oid)
        )
        print(
            _("{path} :: {description}").format(
                path=f"{bold}{path}{reset}",
                description=f"{color}{description}{reset}",
            )
        )
        return

    color = Colors.YELLOW if use_color else ""
    description = _("Submodule pointer modified")
    print(
        _("{path} :: {description}").format(
            path=f"{bold}{path}{reset}",
            description=f"{color}{description}{reset}",
        )
    )
    print(_("old {oid}").format(oid=_short_oid(gitlink_change.old_oid)))
    print(_("new {oid}").format(oid=_short_oid(gitlink_change.new_oid)))


def print_rename_change(rename_change: RenameChange) -> None:
    """Print a file rename as an atomic structural change."""
    use_color = Colors.enabled()

    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""
    color = Colors.YELLOW if use_color else ""
    description = _("Renamed file")

    print(
        _("{old} -> {new} :: {description}").format(
            old=f"{bold}{rename_change.old_path}{reset}",
            new=f"{bold}{rename_change.new_path}{reset}",
            description=f"{color}{description}{reset}",
        )
    )


def print_text_file_deletion_change(deletion_change: TextFileDeletionChange) -> None:
    """Print a whole-text-file deletion as an atomic path change."""
    use_color = Colors.enabled()

    reset = Colors.RESET if use_color else ""
    bold = Colors.BOLD if use_color else ""
    color = Colors.RED if use_color else ""
    description = _("Deleted text file")

    print(
        _("{path} :: {description}").format(
            path=f"{bold}{deletion_change.path()}{reset}",
            description=f"{color}{description}{reset}",
        )
    )


def _short_oid(oid: str | None) -> str:
    """Return a compact object id for display."""
    return oid[:12] if oid else _("unknown")
