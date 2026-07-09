"""Snippet formatting primitives for operation candidate previews."""

from __future__ import annotations

from dataclasses import dataclass


CANDIDATE_GUTTER_SEPARATOR = "│"

_CANDIDATE_OVERVIEW_MAX_LINE_WIDTH = 64


@dataclass(frozen=True)
class CandidateSnippetLine:
    line_number: int | None
    marker: str
    text: str
    highlight: bool = False

    def plain(self, *, width: int) -> str:
        line_number = (
            " " * width
            if self.line_number is None
            else f"{self.line_number:>{width}}"
        )
        return (
            f"{line_number}{CANDIDATE_GUTTER_SEPARATOR} "
            f"{self.marker}{shorten_candidate_overview_text(self.text)}"
        )


def shorten_candidate_overview_text(
    text: str,
    max_width: int = _CANDIDATE_OVERVIEW_MAX_LINE_WIDTH,
) -> str:
    compact = text.strip()
    if len(compact) <= max_width:
        return compact
    if max_width <= 3:
        return compact[:max_width]
    return compact[: max_width - 3] + "..."


def snippet_line_width(lines: tuple[CandidateSnippetLine, ...]) -> int:
    numbered_lines = [line for line in lines if line.line_number is not None]
    if not numbered_lines:
        return 1
    return max(len(str(line.line_number)) for line in numbered_lines)


def plain_candidate_snippet_lines(
    lines: tuple[CandidateSnippetLine, ...],
) -> tuple[str, ...]:
    width = snippet_line_width(lines)
    return tuple(line.plain(width=width) for line in lines)


def candidate_line_in_range(
    line_number: int | None,
    line_range: tuple[int, int] | None,
) -> bool:
    if line_number is None or line_range is None:
        return False
    start, end = line_range
    return start <= line_number <= end
