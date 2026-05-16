"""Exception classes for git-stage-batch."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal


class CommandError(Exception):
    """Raised when a command fails and needs to exit with an error code."""

    def __init__(self, message: str, exit_code: int = 1):
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


def exit_with_error(message: str, exit_code: int = 1) -> None:
    """Raise a CommandError instead of exiting directly."""
    raise CommandError(message, exit_code)


class QuitInteractive(Exception):
    """Raised to exit the interactive mode main loop."""
    pass


class BypassRefresh(Exception):
    """Raised when an action should not refresh the display."""
    pass


class MergeError(Exception):
    """Raised when batch merge fails due to structural ambiguity."""
    pass


@dataclass(frozen=True)
class MergeAmbiguityChoice:
    """One concrete merge ambiguity choice."""

    choice_index: int
    target_after_line: int | None
    target_before_line: int | None
    explanation: str


@dataclass(frozen=True)
class MergeAmbiguity:
    """An enumerable merge ambiguity."""

    key: str
    kind: Literal[
        "missing_claimed_run_placement",
        "ambiguous_absence_boundary",
        "replacement_region_placement",
    ]
    source_line_range: tuple[int, int] | None
    choices: tuple[MergeAmbiguityChoice, ...]


@dataclass(frozen=True)
class MergeAmbiguitySet:
    """A collection of merge ambiguities for one target."""

    file_path: str | None
    source_line_count: int
    target_line_count: int
    ambiguities: tuple[MergeAmbiguity, ...]


class MergeAmbiguityError(MergeError):
    """Raised when a merge refusal has enumerable candidate choices."""

    def __init__(self, ambiguity: MergeAmbiguitySet):
        self.ambiguity = ambiguity
        super().__init__("Merge has enumerable ambiguity")


class MissingAnchorError(MergeError):
    """Raised when an anchor line is not present in realized content.

    This is a recoverable condition during discard: the absence claim
    is simply not applicable to the current state.
    """
    pass


class AmbiguousAnchorError(MergeError):
    """Raised when an anchor line appears multiple times without clear precedence.

    This is not recoverable: structural ambiguity means we cannot determine
    the correct boundary for the deletion.
    """
    pass


class AtomicUnitError(MergeError):
    """Raised when attempting to partially select an atomic ownership unit.

    Atomic units (replacements, deletion-only) must be selected completely
    or not at all. Partial selection would produce inconsistent ownership.

    Attributes:
        required_selection_ids: Selection IDs that must be selected together
        unit_kind: Kind of unit (for error messages)
    """
    def __init__(
        self,
        message: str,
        required_selection_ids: Iterable[int] | None = None,
        unit_kind: str | None = None,
    ):
        super().__init__(message)
        self.required_selection_ids = required_selection_ids
        self.unit_kind = unit_kind


class NoMoreHunks(Exception):
    """Raised when there are no more hunks or binary files to process."""
    pass


class BatchMetadataError(Exception):
    """Raised when batch metadata is missing, corrupted, or structurally invalid.

    This represents infrastructure/state corruption distinct from semantic
    merge conflicts or batch content issues.
    """
    pass
