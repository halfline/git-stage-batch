"""Realized batch entry views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RealizedEntry:
    """A line view in realized content with structural provenance.

    Tracks where each line came from in batch-source space, enabling
    exact anchored boundary resolution for absence constraints.
    """

    content: Any  # Line content with newline
    source_line: int | None  # Batch-source line number, or None for extras
    target_line: int | None = None  # Working-tree line number, when known
    is_claimed: bool = False  # True if from a claimed source line
