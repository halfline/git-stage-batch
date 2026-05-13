"""Batch name validation and existence checking."""

from __future__ import annotations

from .ref_names import BATCH_CONTENT_REF_PREFIX, LEGACY_BATCH_REF_PREFIX
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
    """Check if a batch exists by checking its authoritative git ref."""
    result = run_git_command(
        ["show-ref", "--verify", "--quiet", f"{BATCH_CONTENT_REF_PREFIX}{batch_name}"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode == 0:
        return True

    result = run_git_command(
        ["show-ref", "--verify", "--quiet", f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 0
