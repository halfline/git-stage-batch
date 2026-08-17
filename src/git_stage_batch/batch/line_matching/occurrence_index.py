"""Storage-backed target-line occurrence indexing."""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Iterator, Sequence, Sized

from ...core.mapped_storage import MappedIntVector, MappedRecordVector
from ...core.text_lines import normalize_line_endings
from .match_workspace import MatcherWorkspace


_MAX_UINT64 = (1 << 64) - 1
_CONTENT_RECORD_FORMAT = "QQQQQ"
_CONTENT_HASH = 0
_CONTENT_REPRESENTATIVE_INDEX = 1
_CONTENT_COUNT = 2
_CONTENT_FIRST_POSITION = 3
_CONTENT_NEXT = 4
_POSITION_RECORD_FORMAT = "QQ"
_POSITION_TARGET_INDEX = 0
_POSITION_NEXT = 1
_BOUNDARY_RECORD_FORMAT = "QQQQ"
_BOUNDARY_HASH = 0
_BOUNDARY_REPRESENTATIVE_POSITION = 1
_BOUNDARY_COUNT = 2
_BOUNDARY_NEXT = 3


def normalized_line_payload(content: bytes) -> bytes:
    """Return normalized line bytes without the final line terminator."""
    normalized = normalize_line_endings(bytes(content))
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


class LinePayloadOccurrenceIndex:
    """Index target positions by line payload using mapped storage."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        target_lines: Sequence[bytes],
        *,
        normalize_payloads: bool = True,
        target_indexes: Iterable[int] | None = None,
    ) -> None:
        self._target_lines = target_lines
        self._normalize_payloads = normalize_payloads
        self._indexes_all_target_lines = target_indexes is None
        index_capacity = (
            len(target_indexes)
            if isinstance(target_indexes, Sized)
            else len(target_lines)
        )
        self._workspace: MatcherWorkspace | None = workspace
        self._bucket_count = _bucket_capacity(index_capacity)
        self._buckets: MappedIntVector
        self._contents: MappedRecordVector
        self._positions: MappedRecordVector
        self._boundary_buckets: MappedIntVector | None = None
        self._boundaries: MappedRecordVector | None = None
        try:
            self._buckets = workspace.int_vector(
                self._bucket_count,
                width=8,
                fill=0,
            )
            self._contents = workspace.record_vector(
                index_capacity,
                _CONTENT_RECORD_FORMAT,
            )
            self._positions = workspace.record_vector(
                index_capacity,
                _POSITION_RECORD_FORMAT,
            )
            self._build(target_indexes)
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def close(self) -> None:
        """Release this index's mapped storage before its workspace closes."""
        workspace = self._workspace
        if workspace is None:
            return
        first_error: BaseException | None = None
        for resource in (
            getattr(self, "_boundaries", None),
            getattr(self, "_boundary_buckets", None),
            getattr(self, "_positions", None),
            getattr(self, "_contents", None),
            getattr(self, "_buckets", None),
        ):
            if resource is None:
                continue
            try:
                workspace.close_resource(resource)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        self._workspace = None

    def unique_adjacent_boundary_position(
        self,
        after_content: bytes,
        before_content: bytes,
    ) -> int | None:
        """Return the sole boundary between two payloads, if there is one.

        The adjacent-payload index is built once in mapped storage. This lets
        callers distinguish a unique ``A | B`` boundary even when each payload
        occurs many times, without rescanning all ``A`` or ``B`` occurrences
        for every claimed insertion run.
        """
        if not self._indexes_all_target_lines:
            raise ValueError("adjacent-boundary queries require a full target index")
        self._ensure_boundary_index()
        assert self._boundaries is not None
        assert self._boundary_buckets is not None

        after_payload = self._payload(after_content)
        before_payload = self._payload(before_content)
        record_index = self._find_boundary_record(
            after_payload,
            before_payload,
            _payload_pair_hash(after_payload, before_payload),
            self._boundaries,
            self._boundary_buckets,
        )
        if record_index is None:
            return None
        record = self._boundaries[record_index]
        if record[_BOUNDARY_COUNT] != 1:
            return None
        return record[_BOUNDARY_REPRESENTATIVE_POSITION]

    def occurrence_count(self, content: bytes) -> int:
        """Return the number of target lines with this configured payload."""
        payload = self._payload(content)
        record_index = self._find_content_record(
            payload,
            _payload_hash(payload),
        )
        if record_index is None:
            return 0
        return self._contents[record_index][_CONTENT_COUNT]

    def matching_line_indexes(self, content: bytes) -> Iterator[int]:
        """Yield zero-based target indexes having this configured payload."""
        payload = self._payload(content)
        record_index = self._find_content_record(
            payload,
            _payload_hash(payload),
        )
        if record_index is None:
            return

        position_number = self._contents[record_index][_CONTENT_FIRST_POSITION]
        while position_number != 0:
            position = self._positions[position_number - 1]
            yield position[_POSITION_TARGET_INDEX]
            position_number = position[_POSITION_NEXT]

    def _build(self, target_indexes: Iterable[int] | None) -> None:
        indexes = (
            range(len(self._target_lines)) if target_indexes is None else target_indexes
        )
        for target_index in indexes:
            if target_index < 0 or target_index >= len(self._target_lines):
                raise ValueError("target line index is out of range")
            payload = self._payload(self._target_lines[target_index])
            payload_hash = _payload_hash(payload)
            content_record_index = self._find_content_record(
                payload,
                payload_hash,
            )

            if content_record_index is None:
                bucket_index = self._bucket_index(payload_hash)
                position_index = self._positions.append((target_index, 0))
                content_record_index = self._contents.append(
                    (
                        payload_hash,
                        target_index,
                        1,
                        position_index + 1,
                        self._buckets[bucket_index],
                    )
                )
                self._buckets[bucket_index] = content_record_index + 1
                continue

            record = self._contents[content_record_index]
            position_index = self._positions.append(
                (
                    target_index,
                    record[_CONTENT_FIRST_POSITION],
                )
            )
            self._contents[content_record_index] = (
                record[_CONTENT_HASH],
                record[_CONTENT_REPRESENTATIVE_INDEX],
                record[_CONTENT_COUNT] + 1,
                position_index + 1,
                record[_CONTENT_NEXT],
            )

    def _ensure_boundary_index(self) -> None:
        if self._boundaries is not None:
            return
        workspace = self._workspace
        if workspace is None:
            raise ValueError("occurrence index is closed")

        boundary_capacity = max(0, len(self._target_lines) - 1)
        bucket_count = _bucket_capacity(boundary_capacity)
        boundary_buckets: MappedIntVector | None = None
        boundaries: MappedRecordVector | None = None
        try:
            boundary_buckets = workspace.int_vector(
                bucket_count,
                width=8,
                fill=0,
            )
            boundaries = workspace.record_vector(
                boundary_capacity,
                _BOUNDARY_RECORD_FORMAT,
            )
            for position in range(1, len(self._target_lines)):
                after_payload = self._payload(self._target_lines[position - 1])
                before_payload = self._payload(self._target_lines[position])
                payload_hash = _payload_pair_hash(after_payload, before_payload)
                record_index = self._find_boundary_record(
                    after_payload,
                    before_payload,
                    payload_hash,
                    boundaries,
                    boundary_buckets,
                )
                if record_index is None:
                    bucket_index = payload_hash & (bucket_count - 1)
                    new_record_index = boundaries.append(
                        (
                            payload_hash,
                            position,
                            1,
                            boundary_buckets[bucket_index],
                        )
                    )
                    boundary_buckets[bucket_index] = new_record_index + 1
                    continue

                record = boundaries[record_index]
                if record[_BOUNDARY_COUNT] == 1:
                    boundaries[record_index] = (
                        record[_BOUNDARY_HASH],
                        record[_BOUNDARY_REPRESENTATIVE_POSITION],
                        2,
                        record[_BOUNDARY_NEXT],
                    )
        except BaseException:
            for resource in (boundaries, boundary_buckets):
                if resource is None:
                    continue
                try:
                    workspace.close_resource(resource)
                except BaseException:
                    pass
            raise

        assert boundary_buckets is not None
        assert boundaries is not None
        self._boundary_buckets = boundary_buckets
        self._boundaries = boundaries

    def _find_boundary_record(
        self,
        after_payload: Hashable,
        before_payload: Hashable,
        payload_hash: int,
        boundaries: Sequence[tuple[int, ...]],
        boundary_buckets: Sequence[int],
    ) -> int | None:
        record_number = boundary_buckets[
            payload_hash & (len(boundary_buckets) - 1)
        ]
        while record_number != 0:
            record_index = record_number - 1
            record = boundaries[record_index]
            position = record[_BOUNDARY_REPRESENTATIVE_POSITION]
            if (
                record[_BOUNDARY_HASH] == payload_hash
                and self._payload(self._target_lines[position - 1]) == after_payload
                and self._payload(self._target_lines[position]) == before_payload
            ):
                return record_index
            record_number = record[_BOUNDARY_NEXT]
        return None

    def _find_content_record(
        self,
        payload: Hashable,
        payload_hash: int,
    ) -> int | None:
        record_number = self._buckets[self._bucket_index(payload_hash)]

        while record_number != 0:
            record_index = record_number - 1
            record = self._contents[record_index]
            if (
                record[_CONTENT_HASH] == payload_hash
                and self._payload(
                    self._target_lines[record[_CONTENT_REPRESENTATIVE_INDEX]]
                )
                == payload
            ):
                return record_index
            record_number = record[_CONTENT_NEXT]

        return None

    def _bucket_index(self, payload_hash: int) -> int:
        return payload_hash & (self._bucket_count - 1)

    def _payload(self, content: bytes) -> Hashable:
        if self._normalize_payloads:
            return normalized_line_payload(content)
        return content


def _bucket_capacity(line_count: int) -> int:
    capacity = 1
    target_capacity = max(1, line_count * 2)
    while capacity < target_capacity:
        capacity <<= 1
    return capacity


def _payload_hash(payload: Hashable) -> int:
    return hash(payload) & _MAX_UINT64


def _payload_pair_hash(after_payload: Hashable, before_payload: Hashable) -> int:
    """Hash two adjacent payloads without retaining a Python pair object."""
    after_hash = hash(after_payload) & _MAX_UINT64
    before_hash = hash(before_payload) & _MAX_UINT64
    rotated_before = ((before_hash << 1) | (before_hash >> 63)) & _MAX_UINT64
    return (after_hash ^ rotated_before) & _MAX_UINT64
