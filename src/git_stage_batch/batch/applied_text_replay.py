"""Merge a change while preserving batches already applied to the worktree."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
from typing import TYPE_CHECKING

from .discard import discard_batch_from_line_sequences_as_buffer
from .line_matching.match import match_lines
from .merge.merge import merge_batch_from_line_sequences_as_buffer
from .ownership.metadata_loading import acquire_ownership_for_metadata_dict
from .line_matching.sequence_equality import line_sequences_equal
from ..core.buffer import LineBuffer
from ..core.line_selection import LineRanges
from ..exceptions import MergeError
from ..i18n import _
from ..utils.repository_buffers import (
    load_git_blob_as_buffer,
    read_git_object_buffer_or_none,
)

if TYPE_CHECKING:
    from .ownership.model import BatchOwnership
    from .state.metadata_types import BatchFileMetadataDict


@dataclass(frozen=True, slots=True)
class AppliedTextApplication:
    """One text application that can be undone and applied again."""

    batch_name: str
    file_path: str
    baseline_commit: str | None
    source_object_id: str
    file_metadata: BatchFileMetadataDict
    trusted_presence_ranges: tuple[tuple[int, int], ...] = ()
    applied_presence_ranges: tuple[tuple[int, int], ...] = ()
    index_preimage_ranges: tuple[tuple[int, int], ...] = ()
    added_separator_ranges: tuple[tuple[int, int], ...] = ()
    preimage: AppliedTextPreimage | None = None


@dataclass(frozen=True, slots=True)
class AppliedTextPreimage:
    """Exact text that existed immediately before one application."""

    path: Path
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _AcquiredTextApplication:
    record: AppliedTextApplication
    source_lines: Sequence[bytes]
    ownership: BatchOwnership
    baseline_lines: Sequence[bytes]


class AppliedTextReplayContext:
    """The worktree text before the recorded batches were applied."""

    def __init__(
        self,
        applications: tuple[_AcquiredTextApplication, ...],
        base_lines: Sequence[bytes],
        trusted_target_lines: Sequence[bytes] | None,
        *,
        spool_dir: str | Path | None,
    ) -> None:
        self._applications = applications
        self._base_lines = base_lines
        self._trusted_target_lines = trusted_target_lines
        self._spool_dir = spool_dir

    def merge(
        self,
        source_lines: Sequence[bytes],
        ownership: BatchOwnership,
    ) -> LineBuffer:
        """Merge into the earlier text, then restore each applied batch."""
        current: LineBuffer | None = None
        try:
            current = _merge_with_trusted_target(
                source_lines,
                ownership,
                self._base_lines,
                self._trusted_target_lines,
                spool_dir=self._spool_dir,
            )
            for application in self._applications:
                updated = _merge_with_trusted_target(
                    application.source_lines,
                    application.ownership,
                    current,
                    self._trusted_target_lines,
                    spool_dir=self._spool_dir,
                )
                current.close()
                current = updated
            return current
        except BaseException:
            if current is not None:
                current.close()
            raise

def _merge_with_trusted_target(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    *,
    spool_dir: str | Path | None,
) -> LineBuffer:
    """Merge one batch, using unchanged index text when it is available."""
    has_replacement_origin = any(
        unit.origin is not None for unit in ownership.replacement_units
    )
    if trusted_target_lines is None or not has_replacement_origin:
        return merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            spool_dir=spool_dir,
        )

    with ExitStack() as stack:
        source_to_working = stack.enter_context(
            match_lines(source_lines, working_lines, spool_dir=spool_dir)
        )
        source_to_trusted = stack.enter_context(
            match_lines(source_lines, trusted_target_lines, spool_dir=spool_dir)
        )
        trusted_to_working = stack.enter_context(
            match_lines(trusted_target_lines, working_lines, spool_dir=spool_dir)
        )
        return merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            source_to_working_mapping=source_to_working,
            trusted_target_lines=trusted_target_lines,
            source_to_trusted_target_mapping=source_to_trusted,
            trusted_target_to_working_mapping=trusted_to_working,
            spool_dir=spool_dir,
        )


def _discard_application(
    application: _AcquiredTextApplication,
    working_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    *,
    spool_dir: str | Path | None,
) -> LineBuffer:
    record = application.record
    return discard_batch_from_line_sequences_as_buffer(
        application.source_lines,
        application.ownership,
        working_lines,
        application.baseline_lines,
        trusted_presence_lines=LineRanges.from_ranges(record.trusted_presence_ranges),
        trusted_target_lines=trusted_target_lines,
        applied_presence_lines=LineRanges.from_ranges(record.applied_presence_ranges),
        index_preimage_presence_lines=LineRanges.from_ranges(
            record.index_preimage_ranges
        ),
        added_separator_lines=LineRanges.from_ranges(record.added_separator_ranges),
    )


def _replay_applications(
    applications: Sequence[_AcquiredTextApplication],
    base_lines: Sequence[bytes],
    trusted_target_lines: Sequence[bytes] | None,
    *,
    spool_dir: str | Path | None,
) -> LineBuffer:
    current: Sequence[bytes] = base_lines
    owned_current: LineBuffer | None = None
    try:
        for application in applications:
            updated = _merge_with_trusted_target(
                application.source_lines,
                application.ownership,
                current,
                trusted_target_lines,
                spool_dir=spool_dir,
            )
            if owned_current is not None:
                owned_current.close()
            owned_current = updated
            current = updated
        if owned_current is None:
            raise ValueError("applied text replay requires at least one application")
        return owned_current
    except BaseException:
        if owned_current is not None:
            owned_current.close()
        raise


def _load_preimage(
    preimage: AppliedTextPreimage,
    *,
    spool_dir: str | Path | None,
) -> LineBuffer:
    """Load saved predecessor text after checking its size and digest."""
    try:
        metadata = preimage.path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != preimage.size:
            raise MergeError(_("Saved text predecessor is missing or invalid"))
        buffer = LineBuffer.from_path(preimage.path, spool_dir=spool_dir)
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise MergeError(_("Saved text predecessor is missing or invalid")) from error

    try:
        digest = hashlib.sha256()
        for chunk in buffer.byte_chunks():
            digest.update(chunk)
        if digest.hexdigest() != preimage.sha256:
            raise MergeError(_("Saved text predecessor is missing or invalid"))
        return buffer
    except BaseException:
        buffer.close()
        raise


def load_predecessor_before_trailing_batch(
    applications: Sequence[AppliedTextApplication],
    batch_name: str,
    *,
    spool_dir: str | Path | None = None,
) -> LineBuffer | None:
    """Load the state before a batch when that batch was applied last."""
    first_target = next(
        (
            index
            for index, application in enumerate(applications)
            if application.batch_name == batch_name
        ),
        None,
    )
    if first_target is None or any(
        application.batch_name != batch_name
        for application in applications[first_target:]
    ):
        return None
    preimage = applications[first_target].preimage
    if preimage is None:
        return None
    return _load_preimage(preimage, spool_dir=spool_dir)


@contextmanager
def acquire_applied_text_replay_context(
    applications: Sequence[AppliedTextApplication],
    working_lines: Sequence[bytes],
    *,
    trusted_target_lines: Sequence[bytes] | None = None,
    spool_dir: str | Path | None = None,
) -> Iterator[AppliedTextReplayContext]:
    """Temporarily undo valid applications and return the earlier text."""
    if not applications:
        raise ValueError("applied text replay requires at least one application")

    with ExitStack() as resources:
        acquired: list[_AcquiredTextApplication] = []
        for record in applications:
            source_lines = resources.enter_context(
                load_git_blob_as_buffer(
                    record.source_object_id,
                    spool_dir=spool_dir,
                )
            )
            baseline_buffer = (
                None
                if record.baseline_commit is None
                else read_git_object_buffer_or_none(
                    f"{record.baseline_commit}:{record.file_path}",
                    spool_dir=spool_dir,
                )
            )
            baseline_lines: Sequence[bytes] = ()
            if baseline_buffer is not None:
                baseline_lines = resources.enter_context(baseline_buffer)
            ownership = resources.enter_context(
                acquire_ownership_for_metadata_dict(
                    record.file_metadata,
                    spool_dir=spool_dir,
                )
            )
            acquired.append(
                _AcquiredTextApplication(
                    record,
                    source_lines,
                    ownership,
                    baseline_lines,
                )
            )

        base_buffer: LineBuffer | None = None
        try:
            current: Sequence[bytes] = working_lines
            for application in reversed(acquired):
                earlier = _discard_application(
                    application,
                    current,
                    trusted_target_lines,
                    spool_dir=spool_dir,
                )
                if base_buffer is not None:
                    base_buffer.close()
                base_buffer = earlier
                current = earlier

            assert base_buffer is not None
            round_trip = _replay_applications(
                acquired,
                base_buffer,
                trusted_target_lines,
                spool_dir=spool_dir,
            )
            try:
                if not line_sequences_equal(round_trip, working_lines):
                    raise MergeError(
                        _("Recorded text applications do not reproduce the worktree")
                    )
            finally:
                round_trip.close()

            yield AppliedTextReplayContext(
                tuple(acquired),
                base_buffer,
                trusted_target_lines,
                spool_dir=spool_dir,
            )
        finally:
            if base_buffer is not None:
                base_buffer.close()
