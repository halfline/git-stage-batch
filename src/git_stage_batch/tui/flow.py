"""Flow state management for interactive mode source/target tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..i18n import _


class LocationRole(Enum):
    """Role of a flow location in the staging workflow."""
    WORKING_TREE = "working_tree"
    STAGING_AREA = "staging_area"
    BATCH = "batch"


@dataclass(frozen=True)
class FlowLocation:
    """
    A source or target location in the interactive flow.

    Attributes:
        role: The role this location plays in the workflow
        batch_name: Batch name (only for BATCH role)
    """
    role: LocationRole
    batch_name: str | None = None

    @classmethod
    def for_batch(cls, batch_name: str) -> FlowLocation:
        """Create a batch location."""
        return cls(LocationRole.BATCH, batch_name)

    def get_display_label(self) -> str:
        """Get display label for this location."""
        if self.role is LocationRole.WORKING_TREE:
            return _("Working tree")

        if self.role is LocationRole.STAGING_AREA:
            return _("Index")

        assert self.batch_name is not None
        return self.batch_name

    # Constants for common locations
    WORKING_TREE = None  # type: ignore  # Will be set after class definition
    STAGING_AREA = None  # type: ignore


# Set constants after class is fully defined
FlowLocation.WORKING_TREE = FlowLocation(LocationRole.WORKING_TREE)
FlowLocation.STAGING_AREA = FlowLocation(LocationRole.STAGING_AREA)


@dataclass
class FlowState:
    """
    Mutable state tracking source and target for interactive flow.

    Attributes:
        source: Where changes are coming from
        target: Where changes are going to
    """
    source: FlowLocation
    target: FlowLocation
