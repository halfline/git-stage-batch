"""Store complete saved and live versions of a file together."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..core.coordinates import (
    BatchSourceSpace,
    LineSpan,
    SnapshotSpan,
    content_snapshot,
    require_same_snapshot,
)
from ..core.text_lines import normalize_line_endings
from .file_state import SourceBoundOwnership
from .ownership.model import BatchOwnership


@dataclass(frozen=True, slots=True)
class CompleteSourceReplacementAlternative:
    """Where two complete versions of a file are stored in a batch."""

    saved: SnapshotSpan[BatchSourceSpace]
    live: SnapshotSpan[BatchSourceSpace]

    def __post_init__(self) -> None:
        require_same_snapshot(self.saved.snapshot, self.live.snapshot)
        if self.saved.span.start.offset != 0:
            raise ValueError("complete saved replacement does not start at SOF")
        if self.live.span.start != self.saved.span.end:
            raise ValueError("complete replacement alternatives are not adjacent")
        if self.live.span.end.offset != self.live.snapshot.line_count:
            raise ValueError("complete live replacement does not end at EOF")


def _complete_source_replacement_spans(
    source_lines: Sequence[bytes],
    ownership: BatchOwnership,
) -> tuple[LineSpan[BatchSourceSpace], LineSpan[BatchSourceSpace]] | None:
    resolved = ownership.resolve()
    if (
        len(ownership.deletions) != 1
        or len(ownership.replacement_units) != 1
        or len(resolved.replacement_alternatives) != 1
        or not ownership.deletions[0].complete_file_pair
    ):
        return None
    alternative = resolved.replacement_alternatives[0]
    if (
        alternative.deletion_index != 0
        or alternative.unit_index != 0
        or len(alternative.live_payload) != 1
        or alternative.saved.start.offset != 0
        or alternative.live_envelope.end.offset != len(source_lines)
        or resolved.presence_line_set != alternative.saved_lines
        or alternative.absence_claim.anchor.offset != 0
    ):
        return None
    for source_offset, content_line in zip(
        alternative.iter_live_source_offsets(),
        alternative.absence_claim.content_lines,
        strict=True,
    ):
        if normalize_line_endings(bytes(source_lines[source_offset])) != (
            normalize_line_endings(bytes(content_line))
        ):
            return None
    return alternative.saved, alternative.live_payload[0]


def resolve_complete_source_replacement(
    source_lines: Sequence[bytes],
    bound_ownership: SourceBoundOwnership,
) -> CompleteSourceReplacementAlternative | None:
    """Return two versions that together fill the stored source.

    Return ``None`` if the source has other claims. The batch must own exactly
    the first version before the second can be updated directly.
    """
    require_same_snapshot(
        bound_ownership.source_snapshot,
        content_snapshot(
            bound_ownership.source_snapshot.path,
            source_lines,
            space=BatchSourceSpace,
        ),
    )
    spans = _complete_source_replacement_spans(
        source_lines,
        bound_ownership.value,
    )
    if spans is None:
        return None
    saved_span, live_span = spans
    source_snapshot = bound_ownership.source_snapshot
    return CompleteSourceReplacementAlternative(
        saved=SnapshotSpan(source_snapshot, saved_span),
        live=SnapshotSpan(source_snapshot, live_span),
    )
