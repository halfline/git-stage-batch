"""Action plan records for batch-source command execution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.buffer import LineBuffer
from ...core.text_lifecycle import TextFileChangeType


class CloseableResource(Protocol):
    """Resource with explicit lifetime management."""

    def close(self) -> None: ...


class BatchSourceActionPlan(CloseableResource, Protocol):
    """Plan record that may hold resources until command execution."""

    @property
    def file_path(self) -> str: ...


def close_resources(resources: Iterable[CloseableResource]) -> None:
    """Close every resource while preserving the first close failure."""
    first_error: BaseException | None = None
    for resource in resources:
        try:
            resource.close()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


@contextmanager
def resource_cleanup(
    resources: Iterable[CloseableResource],
) -> Iterator[Callable[[], None]]:
    """Close resources once, preserving any error already in flight.

    The yielded callback lets a publisher perform teardown before its
    transaction commits.  Earlier planning failures still close on context
    exit, and a cleanup failure never replaces the primary failure.
    """
    cleanup_attempted = False

    def close_once() -> None:
        nonlocal cleanup_attempted
        if cleanup_attempted:
            return
        cleanup_attempted = True
        close_resources(resources)

    try:
        yield close_once
    except BaseException:
        try:
            close_once()
        except BaseException:
            pass
        raise
    else:
        close_once()


@dataclass
class ApplyTextFileActionPlan:
    """Deferred apply-from text file action with optional merged content."""

    file_path: str
    buffer: LineBuffer | None
    file_mode: str | None
    change_type: TextFileChangeType
    selected_file_metadata: BatchFileMetadataDict | None = None

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass
class IncludeTextFileActionPlan:
    """Deferred include-from text file action with index and worktree content."""

    file_path: str
    index_buffer: LineBuffer | None
    working_buffer: LineBuffer | None
    index_file_mode: str | None
    working_file_mode: str | None
    index_change_type: TextFileChangeType
    working_change_type: TextFileChangeType

    def close(self) -> None:
        if self.index_buffer is not None:
            self.index_buffer.close()
        if (
            self.working_buffer is not None
            and self.working_buffer is not self.index_buffer
        ):
            self.working_buffer.close()


@dataclass
class DiscardTextFileActionPlan:
    """Deferred discard-from text file action with final worktree content."""

    file_path: str
    buffer: LineBuffer | None
    file_mode: str | None
    change_type: TextFileChangeType

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass
class BinaryFileActionPlan:
    """Deferred binary file action with optional stored batch content."""

    file_path: str
    file_meta: BatchFileMetadataDict
    buffer: LineBuffer | None

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass(frozen=True)
class SubmodulePointerActionPlan:
    """Deferred submodule pointer action."""

    file_path: str
    file_meta: BatchFileMetadataDict

    def close(self) -> None:
        return None


def close_action_plans(plans: Iterable[BatchSourceActionPlan]) -> None:
    """Close any resources owned by deferred batch-source action plans."""
    close_resources(plans)
