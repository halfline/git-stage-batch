"""Batch name validation and existence checking."""

from __future__ import annotations

from ..exceptions import exit_with_error
from ..i18n import _
from ..utils.git import run_git_command


def validate_batch_name(name: str) -> None:
    """Validate that a batch name is safe for use in git refs."""
    if not name:
        exit_with_error(_("Batch name cannot be empty"))

    # Check for invalid characters
    invalid_chars = ['/', '\\', '..', ' ', '\t', '\n', '\r']
    for char in invalid_chars:
        if char in name:
            exit_with_error(_("Batch name cannot contain: {char}").format(char=repr(char)))

    # Check for leading dot
    if name.startswith('.'):
        exit_with_error(_("Batch name cannot start with '.'"))


def batch_exists(batch_name: str) -> bool:
    """Check if a batch exists by checking for its git ref."""
    result = run_git_command(
        ["show-ref", "--verify", "--quiet", f"refs/batches/{batch_name}"],
        check=False
    )
    return result.returncode == 0
