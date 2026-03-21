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
