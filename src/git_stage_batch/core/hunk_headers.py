"""Unified diff hunk header helpers."""

from __future__ import annotations

import re

from .models import HunkHeader
from ..exceptions import CommandError


HUNK_HEADER_PATTERN = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@"
)
HUNK_HEADER_PREFIX = b"@@ "


def line_is_hunk_header(line: bytes) -> bool:
    """Return whether a line starts a unified diff hunk."""
    return line.startswith(HUNK_HEADER_PREFIX)


def parse_hunk_header_line(line: bytes) -> HunkHeader:
    """Parse a unified diff hunk header line."""
    captured_header_line = line.decode("utf-8", errors="replace")
    header_match = HUNK_HEADER_PATTERN.match(captured_header_line)
    if not header_match:
        raise CommandError(f"Bad hunk header: {captured_header_line}")

    old_start = int(header_match.group(1))
    old_length = int(header_match.group(2) or "1")
    new_start = int(header_match.group(3))
    new_length = int(header_match.group(4) or "1")
    return HunkHeader(old_start, old_length, new_start, new_length)
