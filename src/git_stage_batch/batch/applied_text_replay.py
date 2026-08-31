from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
from typing import TYPE_CHECKING
from ..core.buffer import LineBuffer
from ..exceptions import MergeError
from ..i18n import _
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
    preimage: AppliedTextPreimage | None = None


@dataclass(frozen=True, slots=True)
class AppliedTextPreimage:
    """Exact text that existed immediately before one application."""

    path: Path
    size: int
    sha256: str


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
