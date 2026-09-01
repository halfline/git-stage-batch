"""Plans for commands that read a batch's saved files."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ...batch.state.metadata_types import BatchFileMetadataDict
from ...core.buffer import LineBuffer
from ...core.resource_cleanup import (
    CloseableResource,
    close_resources_preserving_first,
)
from ...core.text_lifecycle import TextFileChangeType
from ...data.file_target_identity import IndexIdentity


class BatchSourceActionPlan(CloseableResource, Protocol):
    """A plan that may keep files open until the command runs."""

    @property
    def file_path(self) -> str: ...


def close_resources(resources: Iterable[CloseableResource]) -> None:
    """Close every resource while preserving the first close failure."""
    close_resources_preserving_first(resources)


@contextmanager
def resource_cleanup(
    resources: Iterable[CloseableResource],
) -> Iterator[Callable[[], None]]:
    """Close resources once without hiding an earlier error.

    The callback lets the caller close them before committing. They are also
    closed when the context exits. If closing fails while another error is
    already being handled, the earlier error is kept.
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
    """A planned apply to a text file, including merged content when needed."""

    file_path: str
    buffer: LineBuffer | None
    file_mode: str | None
    change_type: TextFileChangeType
    selected_file_metadata: BatchFileMetadataDict | None = None
    introduced_selected_presence: bool = False
    index_preimage_source_ranges: tuple[tuple[int, int], ...] = ()
    expected_index_identity: IndexIdentity | None = None
    added_separator_source_ranges: tuple[tuple[int, int], ...] = ()
    preimage_artifact_path: Path | None = None

    def close(self) -> None:
        if self.buffer is not None:
            self.buffer.close()


@dataclass
class IncludeTextFileActionPlan:
    """A planned include with its index and worktree content."""

    file_path: str
    index_buffer: LineBuffer | None
    working_buffer: LineBuffer | None
    index_file_mode: str | None
    working_file_mode: str | None
    index_change_type: TextFileChangeType
    working_change_type: TextFileChangeType

    def close(self) -> None:
        buffers = []
        if self.index_buffer is not None:
            buffers.append(self.index_buffer)
        if (
            self.working_buffer is not None
            and self.working_buffer is not self.index_buffer
        ):
            buffers.append(self.working_buffer)
        close_resources(buffers)


@dataclass
class DiscardTextFileActionPlan:
    """A planned discard with the resulting worktree content."""

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
    expected_index_identity: IndexIdentity | None = None

    def close(self) -> None:
        return None


def close_action_plans(plans: Iterable[BatchSourceActionPlan]) -> None:
    """Close files held by the plans."""
    close_resources(plans)
