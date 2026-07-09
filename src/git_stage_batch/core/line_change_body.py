"""LineEntry construction for parsed unified diff hunks."""

from __future__ import annotations

from .models import HunkHeader, LineEntry


NO_NEWLINE_MARKER = b"\\ No newline at end of file"


class LineChangeBodyBuilder:
    """Build LineEntry objects from hunk body lines."""

    def __init__(self) -> None:
        self.old_line_number = 0
        self.new_line_number = 0
        self.next_display_id = 0
        self.line_entries: list[LineEntry] = []

    def reset_for_hunk_header(self, header: HunkHeader) -> None:
        """Reset line counters for a parsed hunk header."""
        self.old_line_number = header.old_start
        self.new_line_number = header.new_start
        self.next_display_id = 0

    def append_patch_line(self, line: bytes) -> None:
        """Append one parsed hunk body line."""
        if line.startswith(NO_NEWLINE_MARKER):
            if self.line_entries:
                self.line_entries[-1].has_trailing_newline = False
            return

        if not line:
            sign = " "
            text_bytes = b""
        else:
            sign = line[0:1].decode("ascii")
            text_bytes = line[1:]

        if sign == " ":
            self._append_context_line(text_bytes)
        elif sign == "-":
            self._append_deletion_line(text_bytes)
        elif sign == "+":
            self._append_addition_line(text_bytes)
        else:
            self._append_context_line(text_bytes)

    def _append_context_line(self, text_bytes: bytes) -> None:
        self.line_entries.append(
            LineEntry(
                id=None,
                kind=" ",
                old_line_number=self.old_line_number,
                new_line_number=self.new_line_number,
                text_bytes=text_bytes,
                source_line=None,
            )
        )
        self.old_line_number += 1
        self.new_line_number += 1

    def _append_deletion_line(self, text_bytes: bytes) -> None:
        self.next_display_id += 1
        self.line_entries.append(
            LineEntry(
                id=self.next_display_id,
                kind="-",
                old_line_number=self.old_line_number,
                new_line_number=None,
                text_bytes=text_bytes,
                source_line=None,
            )
        )
        self.old_line_number += 1

    def _append_addition_line(self, text_bytes: bytes) -> None:
        self.next_display_id += 1
        self.line_entries.append(
            LineEntry(
                id=self.next_display_id,
                kind="+",
                old_line_number=None,
                new_line_number=self.new_line_number,
                text_bytes=text_bytes,
                source_line=None,
            )
        )
        self.new_line_number += 1
