"""Exception classes for git-stage-batch."""

from __future__ import annotations


class CommandError(Exception):
    """Raised when a command fails and needs to exit with an error code."""

    def __init__(self, message: str, exit_code: int = 1):
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def exit_with_error(message: str, exit_code: int = 1) -> None:
    """Raise a CommandError instead of exiting directly."""
    raise CommandError(message, exit_code)


class BypassRefresh(Exception):
    """Raised when an action should not refresh the display."""
    pass


class MergeError(Exception):
    """Raised when batch merge fails due to structural ambiguity."""
    pass


class MissingAnchorError(MergeError):
    """Raised when an anchor line is not present in realized content.

    This is a recoverable condition during discard: the deletion claim
    is simply not applicable to the current state.
    """
    pass


class AmbiguousAnchorError(MergeError):
    """Raised when an anchor line appears multiple times without clear precedence.

    This is NOT recoverable: structural ambiguity means we cannot determine
    the correct boundary for the deletion.
    """
    pass


class NoMoreHunks(Exception):
    """Raised when there are no more hunks or binary files to process."""
    pass


class BatchMetadataError(Exception):
    """Raised when batch metadata is missing, corrupted, or structurally invalid.

    This represents infrastructure/state corruption distinct from semantic
    merge conflicts or batch content issues.
    """
    pass
