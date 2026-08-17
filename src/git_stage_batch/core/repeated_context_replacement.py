"""Recognize selected replacement suffixes split by repeated context."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from .models import LineEntry


@dataclass(frozen=True, slots=True)
class RepeatedContextSuffixReplacement:
    """One raw changed run whose selected old/new rows form a suffix pair."""

    first_addition: int
    selected_suffix_start: int


def find_repeated_context_suffix_replacement(
    lines: Sequence[LineEntry],
    selected_ids: Collection[int],
    run_start: int,
    run_end: int,
) -> RepeatedContextSuffixReplacement | None:
    """Return a selected suffix pair split by an unselected context stride.

    Git may anchor a repeated context row inside a logical replacement.  The
    corresponding old/new rows then appear at opposite ends of a raw changed
    run.  Recognize that shape only when the whole deletion side is selected,
    an equal-width addition suffix is selected, and the context immediately
    before the run repeats immediately before that suffix.
    """
    first_addition = run_end
    deletion_count = 0
    for run_index in range(run_start, run_end):
        run_line = lines[run_index]
        if run_line.kind == "-":
            if first_addition != run_end:
                return None
            if run_line.id not in selected_ids:
                return None
            deletion_count += 1
        elif run_line.kind == "+":
            if first_addition == run_end:
                first_addition = run_index
        else:
            return None

    if deletion_count == 0 or first_addition == run_end:
        return None

    selected_suffix_start: int | None = None
    selected_addition_count = 0
    for run_index in range(first_addition, run_end):
        run_line = lines[run_index]
        if run_line.id in selected_ids:
            if selected_suffix_start is None:
                selected_suffix_start = run_index
            selected_addition_count += 1
        elif selected_suffix_start is not None:
            return None

    if (
        selected_suffix_start is None
        or selected_addition_count != deletion_count
        or selected_suffix_start == first_addition
        or run_start == 0
    ):
        return None

    preceding_context = lines[run_start - 1]
    repeated_context = lines[selected_suffix_start - 1]
    if (
        preceding_context.kind != " "
        or preceding_context.old_line_number is None
        or preceding_context.new_line_number is None
        or repeated_context.kind != "+"
        or preceding_context.text_bytes != repeated_context.text_bytes
    ):
        return None

    return RepeatedContextSuffixReplacement(
        first_addition=first_addition,
        selected_suffix_start=selected_suffix_start,
    )
