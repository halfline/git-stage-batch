"""One-line lookahead and streaming validation of unified-diff hunk bodies."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from . import diff_headers as _diff_headers
from . import hunk_headers as _hunk_headers
from ..exceptions import CommandError
from ..i18n import _


class DiffLineCursor:
    """Own an input iterator with at most one buffered line."""

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = iter(lines)
        self._lookahead: bytes | None = None

    def next_line(self) -> bytes | None:
        if self._lookahead is not None:
            line = self._lookahead
            self._lookahead = None
            return line
        return next(self._lines, None)

    def peek_line(self) -> bytes | None:
        if self._lookahead is None:
            self._lookahead = next(self._lines, None)
        return self._lookahead

    def push_back(self, line: bytes) -> None:
        self._lookahead = line

    def close(self) -> None:
        close = getattr(self._lines, "close", None)
        if close is not None:
            close()


def hunk_line_chunks(
    cursor: DiffLineCursor,
    old_file_line: bytes,
    new_file_line: bytes,
    hunk_header_line: bytes,
) -> Iterator[bytes]:
    yield old_file_line + b"\n"
    yield new_file_line + b"\n"
    yield hunk_header_line + b"\n"

    header = _hunk_headers.parse_hunk_header_line(hunk_header_line)
    old_consumed = 0
    new_consumed = 0

    while True:
        body_line = cursor.peek_line()
        old_remaining, new_remaining = header.remaining_body_counts(
            old_consumed,
            new_consumed,
        )

        if body_line is None:
            if old_remaining or new_remaining:
                raise CommandError(_("Diff ended before the hunk body was complete"))
            return

        body_line_stripped = body_line.rstrip(b"\n")

        if body_line_stripped.startswith(b"\\"):
            if body_line_stripped != b"\\ No newline at end of file":
                raise CommandError(_("Invalid marker in diff hunk body"))
            cursor.next_line()
            yield body_line
            continue

        if not old_remaining and not new_remaining:
            if _diff_headers.line_is_diff_git_header(
                body_line_stripped
            ) or _hunk_headers.line_is_hunk_header(body_line_stripped):
                return
            if body_line_stripped.startswith((b" ", b"+", b"-")):
                raise CommandError(_("Diff hunk body exceeds declared counts"))
            raise CommandError(_("Invalid line prefix after diff hunk body"))

        prefix = body_line_stripped[:1]
        if prefix == b" ":
            if not old_remaining or not new_remaining:
                raise CommandError(_("Diff hunk body exceeds declared counts"))
            old_consumed += 1
            new_consumed += 1
        elif prefix == b"-":
            if not old_remaining:
                raise CommandError(_("Diff hunk body exceeds declared old count"))
            old_consumed += 1
        elif prefix == b"+":
            if not new_remaining:
                raise CommandError(_("Diff hunk body exceeds declared new count"))
            new_consumed += 1
        else:
            raise CommandError(_("Invalid line prefix in diff hunk body"))

        cursor.next_line()
        yield body_line
