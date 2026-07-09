"""Position lookup for recorded baseline references."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..utils.text import normalize_line_endings


def _line_payload_for_reference_match(content: Any) -> bytes:
    """Normalize one line for insertion-boundary identity checks."""
    normalized = normalize_line_endings(bytes(content))
    if normalized.endswith(b"\n"):
        return normalized[:-1]
    return normalized


def _reference_line_matches(
    target_line: bytes,
    reference_content: bytes | None,
) -> bool:
    if reference_content is None:
        return False
    return (
        _line_payload_for_reference_match(target_line)
        == _line_payload_for_reference_match(reference_content)
    )


def baseline_reference_insertion_position(
    reference: Any,
    working_lines: Sequence[bytes],
) -> int | None:
    """Return the proven insertion position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position > len(working_lines):
        return None

    verified_boundary = False
    if after_line is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            getattr(reference, "after_content", None),
        ):
            return None
        verified_boundary = True

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        if before_line is None:
            if position != len(working_lines):
                return None
            verified_boundary = True
        else:
            if position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[position],
                getattr(reference, "before_content", None),
            ):
                return None
            verified_boundary = True

    if not verified_boundary:
        return None
    return position


def baseline_reference_absence_position(
    reference: Any,
    working_lines: Sequence[bytes],
    sequence_length: int,
) -> int | None:
    """Return the proven removal position for a baseline reference."""
    if reference is None or not getattr(reference, "has_after_line", False):
        return None

    after_line = getattr(reference, "after_line", None)
    position = after_line or 0
    if position < 0 or position + sequence_length > len(working_lines):
        return None

    after_content = getattr(reference, "after_content", None)
    if after_line is not None and after_content is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            after_content,
        ):
            return None

    if getattr(reference, "has_before_line", False):
        before_line = getattr(reference, "before_line", None)
        before_position = position + sequence_length
        if before_line is None:
            if before_position != len(working_lines):
                return None
        else:
            if before_position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[before_position],
                getattr(reference, "before_content", None),
            ):
                return None

    return position
