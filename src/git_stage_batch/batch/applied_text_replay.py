"""Merge a change while preserving batches already applied to the worktree."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from difflib import SequenceMatcher
import hashlib
from pathlib import Path
import re
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


@dataclass(frozen=True, slots=True)
class _WordToken:
    """One non-whitespace word and its byte offsets."""

    value: bytes
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _WordInsertion:
    """Words inserted at one boundary in the earlier text."""

    boundary: int
    words: tuple[bytes, ...]


_MAX_WORD_COMPOSITION_BYTES = 256 * 1024
_WORD_CONTEXT_LIMIT = 16
_MAX_WORD_INSERTIONS = 8


def _bounded_content(lines: Sequence[bytes]) -> bytes | None:
    """Return small content without allowing file-sized Python allocation."""
    byte_count = 0
    content = bytearray()
    for line in lines:
        chunk = bytes(line)
        byte_count += len(chunk)
        if byte_count > _MAX_WORD_COMPOSITION_BYTES:
            return None
        content.extend(chunk)
    return bytes(content)


def _word_tokens(content: bytes) -> list[_WordToken]:
    """Split bounded content into words while retaining byte positions."""
    return [
        _WordToken(match.group(), match.start(), match.end())
        for match in re.finditer(rb"\S+", content)
    ]


def _insertion_only_word_changes(
    base: Sequence[_WordToken],
    changed: Sequence[_WordToken],
) -> tuple[_WordInsertion, ...] | None:
    """Return added words when every earlier word remains in order."""
    insertions: list[_WordInsertion] = []
    base_index = 0
    changed_index = 0
    while base_index < len(base):
        if (
            changed_index < len(changed)
            and changed[changed_index].value == base[base_index].value
        ):
            base_index += 1
            changed_index += 1
            continue
        insertion_start = changed_index
        while (
            changed_index < len(changed)
            and changed[changed_index].value != base[base_index].value
        ):
            changed_index += 1
        if changed_index == len(changed):
            return None
        insertions.append(
            _WordInsertion(
                base_index,
                tuple(
                    token.value
                    for token in changed[insertion_start:changed_index]
                ),
            )
        )
        if len(insertions) > _MAX_WORD_INSERTIONS:
            return None
    if changed_index < len(changed):
        insertions.append(
            _WordInsertion(
                len(base),
                tuple(token.value for token in changed[changed_index:]),
            )
        )
        if len(insertions) > _MAX_WORD_INSERTIONS:
            return None
    return tuple(insertion for insertion in insertions if insertion.words)


def _unique_contiguous_word_span(
    container: Sequence[_WordToken],
    candidate: Sequence[_WordToken],
) -> bool:
    """Return whether a substantial word sequence occurs exactly once."""
    if len(candidate) < 3 or len(candidate) > len(container):
        return False
    values = tuple(token.value for token in candidate)
    match_count = 0
    for start in range(len(container) - len(values) + 1):
        if tuple(token.value for token in container[start : start + len(values)]) != values:
            continue
        match_count += 1
        if match_count > 1:
            return False
    return match_count == 1


def _insertions_preserving_baseline_words(
    before: Sequence[_WordToken],
    changed: Sequence[_WordToken],
    replacement: Sequence[_WordToken],
) -> tuple[_WordInsertion, ...] | None:
    """Find additions while retaining uniquely identified baseline wording.

    A transformed selection can end before wording that another applied batch
    preserves.  In that case ``changed`` omits those baseline words even though
    its independent addition can still be carried into ``replacement``.
    """
    matcher = SequenceMatcher(
        a=[token.value for token in replacement],
        b=[token.value for token in changed],
        autojunk=False,
    )
    insertions: list[_WordInsertion] = []
    for tag, replacement_start, replacement_end, changed_start, changed_end in (
        matcher.get_opcodes()
    ):
        if tag == "equal":
            continue
        if tag == "delete":
            if not _unique_contiguous_word_span(
                before,
                replacement[replacement_start:replacement_end],
            ):
                return None
            continue
        if tag != "insert":
            return None
        if (
            _matching_word_context(
                replacement,
                replacement_start,
                changed,
                changed_start,
            )
            < min(3, len(replacement))
        ):
            return None
        insertions.append(
            _WordInsertion(
                replacement_start,
                tuple(
                    token.value
                    for token in changed[changed_start:changed_end]
                ),
            )
        )
        if len(insertions) > _MAX_WORD_INSERTIONS:
            return None
    return tuple(insertion for insertion in insertions if insertion.words) or None


def _matching_word_context(
    base: Sequence[_WordToken],
    boundary: int,
    target: Sequence[_WordToken],
    target_boundary: int,
) -> int:
    """Count equal words next to two proposed boundaries."""
    matched = 0
    offset = 1
    while (
        offset <= _WORD_CONTEXT_LIMIT
        and boundary - offset >= 0
        and target_boundary - offset >= 0
        and base[boundary - offset].value
        == target[target_boundary - offset].value
    ):
        matched += 1
        offset += 1
    offset = 0
    while (
        offset < _WORD_CONTEXT_LIMIT
        and boundary + offset < len(base)
        and target_boundary + offset < len(target)
        and base[boundary + offset].value
        == target[target_boundary + offset].value
    ):
        matched += 1
        offset += 1
    return matched


def _unique_target_boundary(
    base: Sequence[_WordToken],
    boundary: int,
    target: Sequence[_WordToken],
) -> int | None:
    """Find one target boundary with the strongest nearby word context."""
    best_boundary: int | None = None
    best_score = 0
    tied = False
    for candidate in range(len(target) + 1):
        score = _matching_word_context(base, boundary, target, candidate)
        if score > best_score:
            best_boundary = candidate
            best_score = score
            tied = False
        elif score == best_score and score > 0:
            tied = True
    required_context = min(3, len(base))
    if tied or best_boundary is None or best_score < required_context:
        return None
    return best_boundary


def _compose_word_insertions(
    before: Sequence[bytes],
    changed: Sequence[bytes],
    replacement: Sequence[bytes],
    *,
    spool_dir: str | Path | None,
) -> LineBuffer | None:
    """Carry unambiguous added words across another paragraph rewrite."""
    before_content = _bounded_content(before)
    changed_content = _bounded_content(changed)
    replacement_content = _bounded_content(replacement)
    if (
        before_content is None
        or changed_content is None
        or replacement_content is None
    ):
        return None
    before_tokens = _word_tokens(before_content)
    changed_tokens = _word_tokens(changed_content)
    replacement_tokens = _word_tokens(replacement_content)
    insertions = _insertion_only_word_changes(before_tokens, changed_tokens)
    replacement_boundaries = False
    if not insertions:
        insertions = _insertions_preserving_baseline_words(
            before_tokens,
            changed_tokens,
            replacement_tokens,
        )
        replacement_boundaries = True
    if not insertions:
        return None

    edits: list[tuple[int, bytes]] = []
    previous_position = -1
    for insertion in insertions:
        target_boundary = (
            insertion.boundary
            if replacement_boundaries
            else _unique_target_boundary(
                before_tokens,
                insertion.boundary,
                replacement_tokens,
            )
        )
        if target_boundary is None:
            return None
        position = (
            len(replacement_content)
            if target_boundary == len(replacement_tokens)
            else replacement_tokens[target_boundary].start
        )
        if position < previous_position:
            return None
        previous_position = position
        prefix = (
            b""
            if position == 0 or replacement_content[position - 1 : position].isspace()
            else b" "
        )
        suffix = (
            b""
            if position == len(replacement_content)
            or replacement_content[position : position + 1].isspace()
            else b" "
        )
        edits.append((position, prefix + b" ".join(insertion.words) + suffix))

    chunks: list[bytes] = []
    cursor = 0
    for position, inserted in edits:
        chunks.append(replacement_content[cursor:position])
        chunks.append(inserted)
        cursor = position
    chunks.append(replacement_content[cursor:])
    return LineBuffer.from_chunks(chunks, spool_dir=spool_dir)


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
            for application_index, application in enumerate(self._applications):
                updated: LineBuffer | None
                try:
                    updated = _merge_with_trusted_target(
                        application.source_lines,
                        application.ownership,
                        current,
                        self._trusted_target_lines,
                        spool_dir=self._spool_dir,
                    )
                except MergeError:
                    with ExitStack() as stack:
                        before_application = self._base_lines
                        if application_index:
                            before_application = stack.enter_context(
                                _replay_applications(
                                    self._applications[:application_index],
                                    self._base_lines,
                                    self._trusted_target_lines,
                                    spool_dir=self._spool_dir,
                                )
                            )
                        replacement = stack.enter_context(
                            _merge_with_trusted_target(
                                application.source_lines,
                                application.ownership,
                                before_application,
                                self._trusted_target_lines,
                                spool_dir=self._spool_dir,
                            )
                        )
                        updated = _compose_word_insertions(
                            before_application,
                            current,
                            replacement,
                            spool_dir=self._spool_dir,
                        )
                    if updated is None:
                        raise
                assert updated is not None
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
