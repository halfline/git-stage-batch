"""Position lookup for recorded baseline references."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.text_lines import normalize_line_endings
from ...editor.piece_table import LineLike
from ..ownership.references import BaselineReference


def _line_payload_for_reference_match(content: LineLike) -> bytes:
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
    reference: BaselineReference | None,
    working_lines: Sequence[bytes],
) -> int | None:
    """Return the proven insertion position for a baseline reference."""
    if reference is None or not reference.has_after_line:
        return None

    after_line = reference.after_line
    position = after_line or 0
    if position < 0 or position > len(working_lines):
        return None

    verified_boundary = False
    if after_line is not None:
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            reference.after_content,
        ):
            return None
        verified_boundary = True

    if reference.has_before_line:
        before_line = reference.before_line
        if before_line is None:
            if position != len(working_lines):
                return None
            verified_boundary = True
        else:
            if position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[position],
                reference.before_content,
            ):
                return None
            verified_boundary = True
    elif position != len(working_lines):
        # Historical EOF additions recorded only their preceding line.  That
        # one-sided reference is exact only while the target still ends there;
        # otherwise structural source mapping must choose the placement.
        return None

    if not verified_boundary:
        return None
    return position


def baseline_reference_absence_position(
    reference: BaselineReference | None,
    working_lines: Sequence[bytes],
    sequence_length: int,
) -> int | None:
    """Return the proven removal position for a baseline reference."""
    if reference is None or not reference.has_after_line:
        return None

    after_line = reference.after_line
    position = after_line or 0
    if position < 0 or position + sequence_length > len(working_lines):
        return None

    after_content = reference.after_content
    if after_line is not None:
        if after_content is None:
            return None
        if after_line < 1 or after_line > len(working_lines):
            return None
        if not _reference_line_matches(
            working_lines[after_line - 1],
            after_content,
        ):
            return None

    if reference.has_before_line:
        before_line = reference.before_line
        before_position = position + sequence_length
        if before_line is None:
            if before_position != len(working_lines):
                return None
        else:
            if before_position >= len(working_lines):
                return None
            if not _reference_line_matches(
                working_lines[before_position],
                reference.before_content,
            ):
                return None

    return position
