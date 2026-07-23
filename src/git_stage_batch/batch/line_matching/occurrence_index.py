"""Storage-backed target-line occurrence indexing."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

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


def normalized_line_payload(content: bytes) -> bytes:
    """Return normalized line bytes without the final line terminator."""
    normalized = normalize_line_endings(bytes(content))
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


class LinePayloadOccurrenceIndex:
    """Index target positions by normalized line payload using mapped storage."""

    def __init__(
        self,
        workspace: MatcherWorkspace,
        target_lines: Sequence[bytes],
    ) -> None:
        self._target_lines = target_lines
        self._bucket_count = _bucket_capacity(len(target_lines))
        self._buckets = workspace.int_vector(
            self._bucket_count,
            width=8,
            fill=0,
        )
        self._contents = workspace.record_vector(
            len(target_lines),
            _CONTENT_RECORD_FORMAT,
        )
        self._positions = workspace.record_vector(
            len(target_lines),
            _POSITION_RECORD_FORMAT,
        )
        self._build()

    def occurrence_count(self, content: bytes) -> int:
        """Return the number of target lines with this normalized payload."""
        payload = normalized_line_payload(content)
        record_index = self._find_content_record(
            payload,
            _payload_hash(payload),
        )
        if record_index is None:
            return 0
        return self._contents[record_index][_CONTENT_COUNT]

    def matching_line_indexes(self, content: bytes) -> Iterator[int]:
        """Yield zero-based target indexes having this normalized payload."""
        payload = normalized_line_payload(content)
        record_index = self._find_content_record(
            payload,
            _payload_hash(payload),
        )
        if record_index is None:
            return

        position_number = self._contents[record_index][
            _CONTENT_FIRST_POSITION
        ]
        while position_number != 0:
            position = self._positions[position_number - 1]
            yield position[_POSITION_TARGET_INDEX]
            position_number = position[_POSITION_NEXT]

    def _build(self) -> None:
        for target_index in range(len(self._target_lines)):
            payload = normalized_line_payload(self._target_lines[target_index])
            payload_hash = _payload_hash(payload)
            content_record_index = self._find_content_record(
                payload,
                payload_hash,
            )

            if content_record_index is None:
                bucket_index = self._bucket_index(payload_hash)
                position_index = self._positions.append((target_index, 0))
                content_record_index = self._contents.append((
                    payload_hash,
                    target_index,
                    1,
                    position_index + 1,
                    self._buckets[bucket_index],
                ))
                self._buckets[bucket_index] = content_record_index + 1
                continue

            record = self._contents[content_record_index]
            position_index = self._positions.append((
                target_index,
                record[_CONTENT_FIRST_POSITION],
            ))
            self._contents[content_record_index] = (
                record[_CONTENT_HASH],
                record[_CONTENT_REPRESENTATIVE_INDEX],
                record[_CONTENT_COUNT] + 1,
                position_index + 1,
                record[_CONTENT_NEXT],
            )

    def _find_content_record(
        self,
        payload: bytes,
        payload_hash: int,
    ) -> int | None:
        record_number = self._buckets[self._bucket_index(payload_hash)]

        while record_number != 0:
            record_index = record_number - 1
            record = self._contents[record_index]
            if (
                record[_CONTENT_HASH] == payload_hash
                and normalized_line_payload(
                    self._target_lines[
                        record[_CONTENT_REPRESENTATIVE_INDEX]
                    ]
                )
                == payload
            ):
                return record_index
            record_number = record[_CONTENT_NEXT]

        return None

    def _bucket_index(self, payload_hash: int) -> int:
        return payload_hash & (self._bucket_count - 1)


def _bucket_capacity(line_count: int) -> int:
    capacity = 1
    target_capacity = max(1, line_count * 2)
    while capacity < target_capacity:
        capacity <<= 1
    return capacity


def _payload_hash(payload: bytes) -> int:
    return hash(payload) & _MAX_UINT64
