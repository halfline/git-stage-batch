"""Range-compressed exact-line ancestry evidence for fixup units."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ..utils.git_command import stream_git_command
from ..utils.git_repository import object_id_hex_length
from .models import (
    FixupRange,
    FixupUnit,
    LineageEvidence,
    LineageHistoryEvidence,
)


@dataclass(frozen=True, slots=True)
class _BlameRangeSummary:
    """Scalar result of one incremental blame range."""

    resolved_line_count: int
    in_range_line_count: int
    candidate_witnesses: tuple[str, ...]


def _incremental_header(
    line: bytes,
    *,
    object_id_width: int,
) -> tuple[str, int] | None:
    token_end = line.find(b" ")
    if token_end < 0:
        return None
    token = line[:token_end].lstrip(b"^")
    if len(token) != object_id_width or not token.strip(b"0"):
        return None
    try:
        int(token, 16)
    except ValueError:
        return None

    # Incremental metadata can contain an arbitrarily long commit summary.
    # Split only after the fixed-width object ID proves this is a header.
    fields = line.split()
    if len(fields) != 4:
        return None
    try:
        int(fields[1])
        int(fields[2])
        line_count = int(fields[3])
    except ValueError:
        return None
    if line_count <= 0:
        raise ValueError("incremental blame returned a non-positive line count")
    return token.decode("ascii"), line_count


def _blame_range(
    path: str,
    start: int,
    end: int,
    *,
    head_commit: str,
    in_range: set[str],
    object_id_width: int,
) -> _BlameRangeSummary:
    expected_line_count = end - start + 1
    resolved_line_count = 0
    in_range_line_count = 0
    candidate_witnesses: list[str] = []
    for line in stream_git_command(
        [
            "blame",
            "--root",
            "--incremental",
            "--follow",
            "--no-ignore-revs-file",
            "-L",
            f"{start},{end}",
            head_commit,
            "--",
            path,
        ],
        requires_index_lock=False,
        literal_pathspecs=True,
    ):
        header = _incremental_header(line, object_id_width=object_id_width)
        if header is None:
            continue
        commit, line_count = header
        resolved_line_count += line_count
        if resolved_line_count > expected_line_count:
            raise ValueError("incremental blame exceeded the requested range")
        if commit not in in_range:
            continue
        in_range_line_count += line_count
        if commit not in candidate_witnesses and len(candidate_witnesses) < 2:
            candidate_witnesses.append(commit)

    if resolved_line_count != expected_line_count:
        raise ValueError("incremental blame did not cover the requested range")
    return _BlameRangeSummary(
        resolved_line_count=resolved_line_count,
        in_range_line_count=in_range_line_count,
        candidate_witnesses=tuple(candidate_witnesses),
    )


def lineage_query_ranges(unit: FixupUnit) -> tuple[tuple[int, int], ...]:
    """Return the unit's exact, normalized source ranges."""
    if unit.lineage_ranges:
        return unit.lineage_ranges
    if unit.old_start is not None and unit.old_len:
        return ((unit.old_start, unit.old_start + unit.old_len - 1),)
    return tuple((line_number, line_number) for line_number in unit.anchor_line_numbers)


def analyze_lineage(
    unit: FixupUnit,
    commit_range: FixupRange,
) -> LineageEvidence:
    """Summarize incremental blame without retaining per-line observations."""
    queried_ranges = lineage_query_ranges(unit)
    queried_line_count = sum(end - start + 1 for start, end in queried_ranges)
    in_range = set(commit_range.commits_newest_first)
    object_id_width = object_id_hex_length()
    resolved_line_count = 0
    in_range_line_count = 0
    candidate_witnesses: list[str] = []

    for start, end in queried_ranges:
        try:
            summary = _blame_range(
                unit.path,
                start,
                end,
                head_commit=commit_range.head_commit,
                in_range=in_range,
                object_id_width=object_id_width,
            )
        except (OSError, ValueError, subprocess.CalledProcessError):
            continue
        resolved_line_count += summary.resolved_line_count
        in_range_line_count += summary.in_range_line_count
        for commit in summary.candidate_witnesses:
            if commit not in candidate_witnesses and len(candidate_witnesses) < 2:
                candidate_witnesses.append(commit)

    ordered_candidates = tuple(
        commit
        for commit in commit_range.commits_newest_first
        if commit in candidate_witnesses
    )
    conclusive = (
        queried_line_count > 0
        and resolved_line_count == queried_line_count
        and len(ordered_candidates) == 1
        and (
            unit.kind == "text-addition"
            or in_range_line_count == queried_line_count
        )
    )
    return LineageEvidence(
        candidates=ordered_candidates,
        queried_ranges=queried_ranges,
        queried_line_count=queried_line_count,
        resolved_line_count=resolved_line_count,
        in_range_line_count=in_range_line_count,
        conclusive=conclusive,
    )


def analyze_lineage_history(
    unit: FixupUnit,
    commit_range: FixupRange,
) -> LineageHistoryEvidence:
    """Find in-range commits that touched any exact source range.

    One ``git log -L`` process is used per disjoint range. Candidate storage is
    bounded by the number of commits in the already-materialized fixup range,
    rather than by the number of selected lines or log records.
    """
    queried_ranges = lineage_query_ranges(unit)
    object_id_width = object_id_hex_length()
    in_range = set(commit_range.commits_newest_first)
    candidate_witnesses: set[str] = set()
    completed_range_count = 0

    for start, end in queried_ranges:
        try:
            for line in stream_git_command(
                [
                    "log",
                    "-L",
                    f"{start},{end}:{unit.path}",
                    f"{commit_range.base_commit}..{commit_range.head_commit}",
                    "--format=%H",
                    "--no-patch",
                ],
                requires_index_lock=False,
                literal_pathspecs=True,
            ):
                token = line.strip()
                if len(token) != object_id_width:
                    continue
                try:
                    commit = token.decode("ascii")
                    int(commit, 16)
                except (UnicodeDecodeError, ValueError):
                    continue
                if commit in in_range:
                    candidate_witnesses.add(commit)
        except (OSError, subprocess.CalledProcessError):
            continue
        completed_range_count += 1

    return LineageHistoryEvidence(
        candidates=tuple(
            commit
            for commit in commit_range.commits_newest_first
            if commit in candidate_witnesses
        ),
        queried_ranges=queried_ranges,
        completed_range_count=completed_range_count,
    )
