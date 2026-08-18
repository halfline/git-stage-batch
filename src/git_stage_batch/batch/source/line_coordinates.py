"""Source-coordinate translation for combined diff displays."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from ...core.models import LineEntry
from ...core.coordinates import BatchSourceSpace, WorktreeSpace
from ..line_matching.transforms import BatchSourceExactTransform


@dataclass(frozen=True, slots=True)
class ExactTransformSourceCoordinates:
    """Line annotation backed by snapshot-validated exact transforms."""

    source_transform: BatchSourceExactTransform[
        BatchSourceSpace,
        BatchSourceSpace,
    ]
    working_transform: BatchSourceExactTransform[
        WorktreeSpace,
        BatchSourceSpace,
    ]

    def translate_working_line(self, line_number: int) -> int | None:
        return self.working_transform.translate_line_number(line_number)

    def translate_existing_source_line(self, line_number: int) -> int | None:
        return self.source_transform.translate_line_number(line_number)


def translate_display_source_coordinates(
    lines: Iterable[LineEntry],
    map_working_line: Callable[[int], int | None],
    *,
    map_existing_source_line: Callable[[int], int | None] | None = None,
) -> Iterator[tuple[LineEntry, int | None]]:
    """Yield each display row with its translated source coordinate.

    The running delta translates old-side deletion positions into the working
    file. Synthetic gaps reset structural context between hunks, but the delta
    remains valid across the omitted unchanged region.
    """
    last_source_line: int | None = None
    coordinate_delta = 0
    deletion_run_anchor: int | None = None
    previous_deleted_old_line: int | None = None

    for line in lines:
        source_line: int | None = None

        if line.kind == " ":
            if line.new_line_number is None:
                last_source_line = None
            else:
                source_line = map_working_line(line.new_line_number)
                if source_line is not None:
                    last_source_line = source_line
                if line.old_line_number is not None:
                    coordinate_delta = (
                        line.new_line_number - line.old_line_number
                    )
            deletion_run_anchor = None
            previous_deleted_old_line = None
        elif line.kind == "+":
            if line.new_line_number is not None:
                source_line = map_working_line(line.new_line_number)
            if source_line is not None:
                last_source_line = source_line
            coordinate_delta += 1
            deletion_run_anchor = None
            previous_deleted_old_line = None
        elif line.kind == "-":
            if not (
                previous_deleted_old_line is not None
                and line.old_line_number == previous_deleted_old_line + 1
            ):
                deletion_run_anchor = last_source_line
                if (
                    deletion_run_anchor is None
                    and line.source_line is not None
                    and map_existing_source_line is not None
                ):
                    deletion_run_anchor = map_existing_source_line(
                        line.source_line
                    )
                if (
                    deletion_run_anchor is None
                    and line.old_line_number is not None
                ):
                    working_anchor = (
                        line.old_line_number - 1 + coordinate_delta
                    )
                    if working_anchor > 0:
                        deletion_run_anchor = map_working_line(working_anchor)
            source_line = deletion_run_anchor
            coordinate_delta -= 1
            previous_deleted_old_line = line.old_line_number
        else:
            deletion_run_anchor = None
            previous_deleted_old_line = None

        yield line, source_line
