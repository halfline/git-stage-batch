"""Tests for conservative replay of legacy ownership intent."""

from contextlib import contextmanager
import gc
import tracemalloc

import pytest

from git_stage_batch.batch.merge import legacy_intent
from git_stage_batch.batch.merge.legacy_intent import (
    reject_ambiguous_legacy_presence_replay,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.claims import PresenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import ReplacementUnit
from git_stage_batch.exceptions import CommandError


SOURCE = [b"head\n", b"owned\n", b"live\n", b"tail\n"]
TARGET = [b"head\n", b"live\n", b"tail\n"]


class _RepeatedLines:
    """Large indexed sequence backed by one immutable line value."""

    def __init__(self, line_count: int):
        self.line_count = line_count

    def __len__(self) -> int:
        return self.line_count

    def __getitem__(self, index: int) -> bytes:
        if not 0 <= index < self.line_count:
            raise IndexError(index)
        return b"line\n"


class _IdentityMapping:
    """Count constant-time source mapping lookups."""

    def __init__(self):
        self.lookup_count = 0

    def get_target_line_from_source_line(self, source_line: int) -> int:
        self.lookup_count += 1
        return source_line


def _reject(ownership: BatchOwnership, *, legacy: bool = True) -> None:
    reject_ambiguous_legacy_presence_replay(
        "file.txt",
        SOURCE,
        ownership,
        TARGET,
        legacy_unmarked_source_alternatives=legacy,
    )


def test_legacy_unmarked_insertion_in_collapsed_gap_is_rejected():
    ownership = BatchOwnership.from_presence_lines(["2"])

    with pytest.raises(CommandError, match="does not record whether"):
        _reject(ownership)


def test_current_metadata_can_replay_an_explicit_insertion():
    ownership = BatchOwnership.from_presence_lines(["2"])

    _reject(ownership, legacy=False)


def test_legacy_insertion_with_exact_saved_boundary_is_accepted():
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        baseline_references={
            2: BaselineReference(
                after_line=1,
                after_content=b"head\n",
                before_line=2,
                before_content=b"live\n",
                has_before_line=True,
            ),
        },
    )

    _reject(ownership)


def test_legacy_replacement_unit_supplies_old_side_intent():
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [AbsenceClaim(anchor_line=1, content_lines=[b"live\n"])],
        replacement_units=[
            ReplacementUnit(presence_lines=["2"], deletion_indices=[0]),
        ],
    )

    _reject(ownership)


def test_replacement_presence_lines_are_built_without_repeated_unions(
    monkeypatch,
):
    """Many compact replacement units should be normalized in one pass."""
    ownership = BatchOwnership.from_presence_lines(
        ["1-1000"],
        replacement_units=[
            ReplacementUnit(
                presence_lines=[str(source_line)],
                deletion_indices=[0],
            )
            for source_line in range(1, 1001)
        ],
    )

    def fail_union(*_args, **_kwargs):
        raise AssertionError("replacement ranges were rebuilt incrementally")

    monkeypatch.setattr(
        "git_stage_batch.batch.merge.legacy_intent.LineRanges.union",
        fail_union,
    )

    replacement_presence = legacy_intent._replacement_presence_lines(ownership)

    assert replacement_presence.ranges() == ((1, 1000),)


def test_legacy_intent_check_uses_linear_lookups_and_bounded_heap(monkeypatch):
    """Large compact selections must not expand into Python line objects."""
    mappings: list[_IdentityMapping] = []

    @contextmanager
    def identity_match(*_args, **_kwargs):
        mapping = _IdentityMapping()
        mappings.append(mapping)
        yield mapping

    monkeypatch.setattr(
        "git_stage_batch.batch.merge.legacy_intent.match_lines",
        identity_match,
    )

    def peak_for(line_count: int) -> int:
        lines = _RepeatedLines(line_count)
        ownership = BatchOwnership.from_presence_lines(
            [f"1-{line_count}"],
        )
        gc.collect()
        tracemalloc.start()
        try:
            reject_ambiguous_legacy_presence_replay(
                "file.txt",
                lines,
                ownership,
                lines,
                legacy_unmarked_source_alternatives=True,
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert mappings[-1].lookup_count == line_count
        return peak_heap

    small_peak = peak_for(128)
    large_peak = peak_for(32_768)

    assert large_peak < small_peak + 128 * 1024


def test_legacy_saved_boundaries_do_not_rescan_presence_claims() -> None:
    """Many v1 claims and missing lines should use one mapped reference index."""
    claimed_line_count = 1500
    source = [
        b"head\n",
        *(f"owned {index}\n".encode() for index in range(claimed_line_count)),
        b"tail\n",
    ]
    target = [b"head\n", b"tail\n"]
    reference = BaselineReference(
        after_line=1,
        after_content=b"head\n",
        before_line=2,
        before_content=b"tail\n",
        has_before_line=True,
    )
    ownership = BatchOwnership(
        [
            PresenceClaim(
                [str(source_line)],
                {source_line: reference},
            )
            for source_line in range(2, claimed_line_count + 2)
        ],
        [],
    )

    reject_ambiguous_legacy_presence_replay(
        "file.txt",
        source,
        ownership,
        target,
        legacy_unmarked_source_alternatives=True,
    )
