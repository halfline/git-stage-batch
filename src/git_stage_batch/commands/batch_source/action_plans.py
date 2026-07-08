"""Action plan records for batch-source command execution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ...core.buffer import LineBuffer


class BatchSourceActionPlan(Protocol):
    """Plan record that may hold resources until command execution."""

    file_path: str

    def close(self) -> None:
        ...


@dataclass
class BinaryFileActionPlan:
    """Deferred binary file action with optional stored batch content."""

    file_path: str
    file_meta: dict
    buffer: LineBuffer | None

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass(frozen=True)
class SubmodulePointerActionPlan:
    """Deferred submodule pointer action."""

    file_path: str
    file_meta: dict

    def close(self) -> None:
        return None


def close_action_plans(plans: Iterable[BatchSourceActionPlan]) -> None:
    """Close any resources owned by deferred batch-source action plans."""
    for plan in plans:
        plan.close()
