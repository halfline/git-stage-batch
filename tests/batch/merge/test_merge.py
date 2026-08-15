"""Tests for structural batch merge algorithm."""

import gc
from itertools import repeat
import tracemalloc

import pytest

import git_stage_batch.batch.merge.baseline_presence_edits as baseline_presence_edits_module
import git_stage_batch.batch.merge.baseline_replacement_edits as baseline_replacement_edits_module
import git_stage_batch.batch.merge.candidate_enumeration as candidate_enumeration_module
import git_stage_batch.batch.merge.merge as merge_module
import git_stage_batch.batch.merge.validation as validation_module
import git_stage_batch.batch.realization.provenance as provenance_module
import git_stage_batch.batch.discard as discard_module
import git_stage_batch.batch.discard_reversal as discard_reversal_module
from git_stage_batch.batch.merge.baseline_correspondence import (
    RegionKind,
    build_baseline_correspondence,
)
from git_stage_batch.batch.merge.baseline_edits import (
    try_apply_baseline_coordinate_edits,
)
from git_stage_batch.batch.discard_reversal import reverse_presence_constraints
from git_stage_batch.batch.discard import (
    _build_realized_entries_for_discard,
    _discard_batch_line_chunks,
    discard_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.merge.candidates import (
    MergeCandidateSetOutcome,
    MergeResolution,
)
from git_stage_batch.batch.merge.absence_constraints import (
    apply_absence_constraints,
)
from git_stage_batch.batch.merge.coordinate_strategy import (
    AMBIGUITY_KEY,
    CoordinateStrategyChoice,
)
from git_stage_batch.batch.merge.validation import check_structural_validity
from git_stage_batch.batch.merge.merge import (
    _merge_batch_line_chunks,
    can_merge_batch_from_line_sequences,
    enumerate_merge_batch_candidates_from_line_sequences,
    merge_batch_from_line_sequences_as_buffer,
)
from git_stage_batch.batch.merge.presence_constraints import satisfy_constraints
from git_stage_batch.batch.merge.presence_context import (
    contextual_presence_placements,
)
from git_stage_batch.batch.realization.entries import RealizedEntry
from git_stage_batch.batch.realization.entry_storage import (
    RealizedEntries,
    realized_entry_content_chunks,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.exceptions import AtomicUnitError, MergeError
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import (
    BatchOwnership,
)
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.text_lines import normalize_line_sequence_endings


_LINE_SCALE_HEAP_LIMIT = 256 * 1024


def test_contextual_presence_does_not_share_unrelated_eof_placement() -> None:
    """One EOF claim cannot waive a sibling run's ordering ambiguity."""
    source = [
        b"head\n",
        b"old one\n",
        b"old two\n",
        b"old three\n",
        b"claimed inner\n",
        b"old tail\n",
        b"claimed edge\n",
    ]
    target = [b"head\n", b"target one\n", b"target two\n"]
    with match_lines(source, target) as mapping:
        with pytest.raises(MergeError, match="different version"):
            contextual_presence_placements(
                source,
                target,
                LineRanges.from_specs(["5", "7"]),
                mapping,
            )


def test_contextual_presence_skips_adjacent_finished_removal_span() -> None:
    """A finished removal span must not hide the next covering span."""
    source = [
        b"head\n",
        b"old one\n",
        b"old two\n",
        b"old three\n",
        b"claimed\n",
        b"old tail\n",
        b"footer\n",
    ]
    target = [b"head\n", b"target one\n", b"target two\n", b"footer\n"]
    with match_lines(source, target) as mapping:
        _missing, placements = contextual_presence_placements(
            source,
            target,
            LineRanges.from_specs(["5"]),
            mapping,
            collapsing_target_spans=((0, 1), (1, 3)),
        )

    assert [placement.gap_index for placement in placements] == [1]


def test_contextual_presence_coalesces_overlapping_removal_spans() -> None:
    """Nested removal spans should form one linear-time collapse region."""
    source = [
        b"head\n",
        b"old one\n",
        b"old two\n",
        b"old three\n",
        b"claimed\n",
        b"old tail\n",
        b"footer\n",
    ]
    target = [b"head\n", b"target one\n", b"target two\n", b"footer\n"]
    with match_lines(source, target) as mapping:
        _missing, placements = contextual_presence_placements(
            source,
            target,
            LineRanges.from_specs(["5"]),
            mapping,
            collapsing_target_spans=((0, 2), (0, 3)),
        )

    assert [placement.gap_index for placement in placements] == [0]


def test_realization_fallback_tracks_target_coordinates_after_removal() -> None:
    """Earlier removals must not stale a later baseline target coordinate."""
    lines = [b"head\n", b"old a\n", b"middle\n", b"old b\n", b"tail\n"]
    entries = RealizedEntries()
    entries.append_line_range_from(
        lines,
        0,
        len(lines),
        source_line_start=1,
        target_line_start=1,
    )
    result = apply_absence_constraints(
        entries,
        [
            AbsenceClaim(anchor_line=4, content_lines=[b"old b\n"]),
            AbsenceClaim(anchor_line=2, content_lines=[b"old a\n"]),
        ],
        strict=False,
        realization_fallback_target_positions=((0, 3), (1, 1)),
    )
    try:
        assert b"".join(realized_entry_content_chunks(result)) == (
            b"head\nmiddle\ntail\n"
        )
    finally:
        result.close()
        entries.close()


class _IndexGuardedLineBuffer(LineBuffer):
    """LineBuffer variant that rejects public line indexing in tests."""

    def __getitem__(self, index):
        raise AssertionError("public line indexing should not be used")


class _IndexGuardedRealizedEntries(RealizedEntries):
    """Realized entries variant that rejects entry-view indexing in tests."""

    def __getitem__(self, index):
        raise AssertionError("entry indexing should not be used")


class _SourceLookupGuardedRealizedEntries(RealizedEntries):
    """Realized entries variant that rejects per-line source lookups in tests."""

    def source_line_at(self, index):
        raise AssertionError("source lookup should not be used")


class _ProvenanceCountingRealizedEntries(RealizedEntries):
    """Realized entries that count streamed provenance runs."""

    def __init__(self):
        super().__init__()
        self.provenance_run_count = 0

    def provenance_runs(self, start=0, stop=None):
        for run in super().provenance_runs(start, stop):
            self.provenance_run_count += 1
            yield run


class _GuardedLine:
    """Hashable line object that rejects materialization."""

    def __init__(self, content: str, hash_value: int | None = None) -> None:
        self.content = content
        self.hash_value = hash(content) if hash_value is None else hash_value

    def __hash__(self) -> int:
        return self.hash_value

    def __eq__(self, other):
        if not isinstance(other, _GuardedLine):
            return NotImplemented
        return self.content == other.content

    def __bytes__(self):
        raise AssertionError("line content should not be materialized")

    def __getitem__(self, index):
        raise AssertionError("line content should not be sliced")

    def endswith(self, suffix):
        raise AssertionError("line endings should not be materialized")


class _CloseTrackingIterator:
    """Chunk iterator that records explicit cleanup."""

    def __init__(self, chunks):
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self):
        self.closed = True


def test_realization_fallback_streams_provenance_once() -> None:
    """Target-ordered fallback lookup must not rescan every earlier run."""
    claim_count = 512
    entries = _ProvenanceCountingRealizedEntries()
    lines = [b"target\n"]
    for line_index in range(claim_count):
        entries.append_line_range_from(
            lines,
            0,
            1,
            source_line_start=line_index * 2 + 1,
            target_line_start=line_index + 1,
        )
    claims = [
        AbsenceClaim(anchor_line=None, content_lines=[b"missing\n"])
        for _ in range(claim_count)
    ]
    fallback_positions = tuple(
        (claim_index, claim_index)
        for claim_index in range(claim_count)
    )

    result = apply_absence_constraints(
        entries,
        claims,
        strict=False,
        realization_fallback_target_positions=fallback_positions,
    )
    try:
        assert result is entries
        assert entries.provenance_run_count < claim_count * 5
    finally:
        result.close()


def test_realization_removal_planning_avoids_line_scale_python_heap() -> None:
    """Per-claim fallback coordinates should remain in mapped storage."""
    claims = [
        AbsenceClaim(anchor_line=None, content_lines=[])
        for _ in range(8192)
    ]

    gc.collect()
    tracemalloc.start()
    try:
        result = satisfy_constraints(
            [],
            [],
            LineRanges.empty(),
            claims,
            strict=False,
        )
        try:
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            result.close()
    finally:
        tracemalloc.stop()

    assert peak_heap < _LINE_SCALE_HEAP_LIMIT


def test_discard_restored_claim_buffer_survives_borrowed_result() -> None:
    """A slice should retain restored spool content after its source closes."""
    claims = [AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])]
    with RealizedEntries() as entries:
        entries.append(b"head\n", source_line=1)
        restored = discard_module._restore_absence_constraints(entries, claims)
        borrowed = restored.slice(1, 2)
        restored.close()
        try:
            assert borrowed.content_at(0) == b"old\n"
        finally:
            borrowed.close()


def test_discard_restoration_closes_converted_predecessor(
    monkeypatch,
) -> None:
    """A changed rollback result releases its internally converted store."""
    captured_entries = []
    original_conversion = discard_module.as_realized_entries

    def capture_conversion(entries):
        converted = original_conversion(entries)
        captured_entries.append(converted)
        return converted

    monkeypatch.setattr(
        discard_module,
        "as_realized_entries",
        capture_conversion,
    )
    entries = [RealizedEntry(b"head\n", source_line=1)]

    restored = discard_module._restore_absence_constraints(
        entries,
        [AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])],
    )
    try:
        assert list(restored.content_chunks()) == [b"head\n", b"old\n"]
        assert len(captured_entries) == 1
        assert captured_entries[0].closed
    finally:
        restored.close()




def test_candidate_comparison_preserves_structural_chunk_boundaries():
    """Stream comparison must not fragment the selected structural output."""
    structural_chunks = [b"alpha\n", b"beta\n", b"gamma\n"]
    coordinate_chunks = [b"", b"alpha\nbe", b"ta\ngamma", b"\n", b""]

    result = list(
        merge_module._yield_identical_candidate_chunks(
            coordinate_chunks,
            structural_chunks,
        )
    )

    assert result == structural_chunks


def test_candidate_comparison_does_not_copy_large_chunk_remainders():
    """Uneven candidate chunks must use constant-size comparison state."""
    structural_chunk = b"x" * 1024
    coordinate_chunk = structural_chunk * 512

    tracemalloc.start()
    try:
        yielded_count = sum(
            1
            for _chunk in merge_module._yield_identical_candidate_chunks(
                (coordinate_chunk,),
                repeat(structural_chunk, 512),
            )
        )
        _current_heap, peak_heap = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert yielded_count == 512
    assert peak_heap < 64 * 1024


def test_distinctive_presence_context_avoids_line_scale_python_heap():
    """Strict contextual indexing should retain records in mapped storage."""
    line_count = 8192
    changed_index = line_count // 2
    source_content = b"".join(
        f"line-{line_index:08d}\n".encode()
        for line_index in range(line_count)
    )
    target_content = b"".join(
        (
            b"target-only\n"
            if line_index == changed_index
            else f"line-{line_index:08d}\n".encode()
        )
        for line_index in range(line_count)
    )
    selected = LineRanges.from_ranges(((changed_index + 1, changed_index + 1),))

    with (
        LineBuffer.from_bytes(source_content) as source_lines,
        LineBuffer.from_bytes(target_content) as target_lines,
        match_lines(source_lines, target_lines) as mapping,
    ):
        gc.collect()
        tracemalloc.start()
        try:
            with pytest.raises(MergeError):
                contextual_presence_placements(
                    source_lines,
                    target_lines,
                    selected,
                    mapping,
                    require_distinctive_context=True,
                )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    assert peak_heap < _LINE_SCALE_HEAP_LIMIT


@pytest.mark.parametrize("candidates_match", [True, False])
def test_coordinate_candidate_is_closed_after_comparison(
    monkeypatch,
    candidates_match,
):
    """Compared coordinate streams must close on acceptance and ambiguity."""
    source = [b"a\n", b"new\n", b"b\n"]
    target = [b"a\n", b"b\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [],
        baseline_references={
            2: BaselineReference(
                after_line=1,
                after_content=b"a\n",
                before_line=2,
                before_content=b"b\n",
                has_before_line=True,
            )
        },
    )
    coordinate_candidate = _CloseTrackingIterator(
        source if candidates_match else [b"a\n", b"b\n", b"new\n"]
    )
    planning_modes = []

    def plan_candidate(*_args, **kwargs):
        trust_coordinates = kwargs.get("trust_baseline_coordinates", False)
        planning_modes.append(trust_coordinates)
        return coordinate_candidate if trust_coordinates else None

    monkeypatch.setattr(
        merge_module._baseline_edits,
        "try_apply_baseline_coordinate_edits",
        plan_candidate,
    )

    if candidates_match:
        with merge_batch_from_line_sequences_as_buffer(
            source,
            ownership,
            target,
        ) as merged:
            assert list(merged) == source
    else:
        with pytest.raises(
            MergeError,
            match="Batch was created from a different version of the file",
        ):
            merge_batch_from_line_sequences_as_buffer(
                source,
                ownership,
                target,
            )

    assert planning_modes == [False, True]
    assert coordinate_candidate.closed


def test_coordinate_candidate_is_closed_when_structural_planning_refuses(
    monkeypatch,
):
    """A coordinate stream must close when no structural candidate exists."""
    source = [b"a\n", b"new\n", b"b\n"]
    target = [b"a\n", b"b\n"]
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [],
        baseline_references={
            2: BaselineReference(
                after_line=1,
                after_content=b"a\n",
                before_line=2,
                before_content=b"b\n",
                has_before_line=True,
            )
        },
    )
    coordinate_candidate = _CloseTrackingIterator(source)

    monkeypatch.setattr(
        merge_module._baseline_edits,
        "try_apply_baseline_coordinate_edits",
        lambda *_args, **kwargs: (
            coordinate_candidate
            if kwargs.get("trust_baseline_coordinates", False)
            else None
        ),
    )

    def refuse_structural_candidate(*_args, **_kwargs):
        raise MergeError("structural candidate is invalid")

    monkeypatch.setattr(
        merge_module,
        "_build_structural_realized_entries",
        refuse_structural_candidate,
    )

    with pytest.raises(MergeError, match="structural candidate is invalid"):
        merge_batch_from_line_sequences_as_buffer(
            source,
            ownership,
            target,
        )

    assert coordinate_candidate.closed


@pytest.mark.parametrize("choice", [0, 3, True, "1", None])
def test_coordinate_strategy_rejects_invalid_resolution_values(choice):
    """Only serialized values of the strategy enum are valid choices."""
    resolution = MergeResolution({AMBIGUITY_KEY: choice})

    with pytest.raises(
        MergeError,
        match="Selected merge resolution is no longer valid",
    ):
        merge_batch_from_line_sequences_as_buffer(
            [b"same\n"],
            BatchOwnership([], []),
            [b"same\n"],
            resolution=resolution,
        )


def test_candidate_enumeration_propagates_atomic_unit_errors(monkeypatch):
    """Candidate discovery must not reinterpret atomic selection failures."""

    def refuse_atomic_selection(*_args, **_kwargs):
        yield from ()
        raise AtomicUnitError("select the complete replacement")

    monkeypatch.setattr(
        merge_module,
        "_merge_batch_acquired_line_chunks",
        refuse_atomic_selection,
    )

    with pytest.raises(
        AtomicUnitError,
        match="select the complete replacement",
    ):
        enumerate_merge_batch_candidates_from_line_sequences(
            [b"source\n"],
            BatchOwnership([], []),
            [b"target\n"],
        )


def test_candidate_enumeration_marks_an_ordinary_merge_as_unambiguous():
    """An empty candidate set must distinguish success from hard refusal."""
    candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
        [b"same\n"],
        BatchOwnership([], []),
        [b"same\n"],
    )

    assert candidate_set.candidates == ()
    assert (
        candidate_set.outcome
        is MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED
    )


def test_large_merge_without_coordinates_plans_baseline_once(monkeypatch):
    """Large legacy batches should not repeat an inapplicable baseline plan."""
    source = [f"line {index}\n".encode() for index in range(10_000)]
    target = [b"remove me\n", *source]
    ownership = BatchOwnership(
        [],
        [AbsenceClaim(anchor_line=None, content_lines=[b"remove me\n"])],
    )
    planning_modes = []
    original_plan = (
        merge_module._baseline_edits.try_apply_baseline_coordinate_edits
    )

    def count_plan(*args, **kwargs):
        planning_modes.append(
            kwargs.get("trust_baseline_coordinates", False)
        )
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(
        merge_module._baseline_edits,
        "try_apply_baseline_coordinate_edits",
        count_plan,
    )

    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
    ) as merged:
        assert len(merged) == len(source)

    assert planning_modes == [False]


def test_unanchored_presence_reuses_fallback_line_mapping(monkeypatch):
    """Baseline probing and structural placement should share one mapping."""
    source = [f"line {index}\n".encode() for index in range(1000)]
    missing_index = len(source) // 2
    target = source[:missing_index] + source[missing_index + 1:]
    ownership = BatchOwnership.from_presence_lines([str(missing_index + 1)], [])
    mapping_calls = 0
    real_match_lines = merge_module.match_lines

    def count_mapping(*args, **kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        return real_match_lines(*args, **kwargs)

    monkeypatch.setattr(merge_module, "match_lines", count_mapping)

    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
    ) as merged:
        assert merged.to_bytes() == b"".join(source)

    assert mapping_calls == 1


def test_reviewed_replacement_reuses_resolution_preflight_mapping(monkeypatch):
    """Reviewed replacement replay should align source and target only once."""
    source = b"a-head\na-new\na-tail\nhead\nnew1\nnew2\ntail\n"
    working = b"a-head\na-new\na-tail\nhead\nold2\ntail\n"
    ownership = _mapped_and_split_replacement_origin_ownership()
    candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
        source.splitlines(keepends=True),
        ownership,
        working.splitlines(keepends=True),
        max_candidates=10,
    )
    assert candidate_set.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED

    mapping_calls = 0
    real_match_lines = merge_module.match_lines

    def count_mapping(*args, **kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        return real_match_lines(*args, **kwargs)

    monkeypatch.setattr(merge_module, "match_lines", count_mapping)

    assert merge_batch(
        source,
        ownership,
        working,
        resolution=candidate_set.candidates[0].resolution,
    ) == b"a-head\na-new\na-tail\nhead\nnew2\ntail\n"
    assert mapping_calls == 1


def test_coordinate_candidate_discovery_reuses_initial_comparison(monkeypatch):
    """Candidate discovery must not repeat both baseline planning passes."""
    source = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEW\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    target = [
        b"P\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"FILL\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"MIDDLE\n",
        b"ANCHOR\n",
        b"NEXT\n",
        b"TAIL\n",
    ]
    ownership = BatchOwnership.from_presence_lines(
        ["6"],
        [],
        baseline_references={
            6: BaselineReference(
                after_line=5,
                after_content=b"ANCHOR\n",
                before_line=6,
                before_content=b"NEXT\n",
                has_before_line=True,
            )
        },
    )
    planning_modes = []
    original_plan = (
        merge_module._baseline_edits.try_apply_baseline_coordinate_edits
    )

    def count_plan(*args, **kwargs):
        planning_modes.append(
            kwargs.get("trust_baseline_coordinates", False)
        )
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(
        merge_module._baseline_edits,
        "try_apply_baseline_coordinate_edits",
        count_plan,
    )

    def fail_duplicate_matching(*_args, **_kwargs):
        raise AssertionError("coordinate candidate discovery repeated line matching")

    monkeypatch.setattr(
        candidate_enumeration_module,
        "match_lines",
        fail_duplicate_matching,
    )

    candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
        source,
        ownership,
        target,
    )

    assert len(candidate_set.candidates) == 2
    assert planning_modes == [False, True]

    planning_modes.clear()
    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
        resolution=candidate_set.candidates[0].resolution,
    ) as _structural_result:
        pass
    assert planning_modes == []

    with merge_batch_from_line_sequences_as_buffer(
        source,
        ownership,
        target,
        resolution=candidate_set.candidates[1].resolution,
    ) as _coordinate_result:
        pass
    assert planning_modes == [True]


def test_merge_routes_mapping_and_output_storage_to_invocation_spool(
    tmp_path,
    monkeypatch,
):
    """Worker merge storage should remain beneath its job scratch directory."""
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    observed_spools = []
    provenance_spools = []
    original_match_lines = merge_module.match_lines
    original_provenance_storage = (
        provenance_module.ChunkedMappedRecordVector
    )

    def record_match(*args, **kwargs):
        observed_spools.append(kwargs.get("spool_dir"))
        return original_match_lines(*args, **kwargs)

    def record_provenance_storage(*args, **kwargs):
        provenance_spools.append(kwargs.get("spool_dir"))
        return original_provenance_storage(*args, **kwargs)

    monkeypatch.setattr(merge_module, "match_lines", record_match)
    monkeypatch.setattr(
        provenance_module,
        "ChunkedMappedRecordVector",
        record_provenance_storage,
    )
    ownership = BatchOwnership.from_presence_lines(["2"], [])
    with (
        LineBuffer.from_bytes(b"one\ninserted\n") as source,
        LineBuffer.from_bytes(b"one\n") as target,
        merge_batch_from_line_sequences_as_buffer(
            source,
            ownership,
            target,
            spool_dir=spool_dir,
        ) as merged,
    ):
        assert merged.to_bytes() == b"one\ninserted\n"

    assert observed_spools == [spool_dir]
    assert provenance_spools
    assert set(provenance_spools) == {spool_dir}


def test_baseline_fallback_routes_matching_storage_to_invocation_spool(
    tmp_path,
    monkeypatch,
):
    """Fallback matching should remain beneath its job scratch directory."""
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    observed_spools = []
    original_match_lines = baseline_presence_edits_module._match_lines

    def record_match(*args, **kwargs):
        observed_spools.append(kwargs.get("spool_dir"))
        return original_match_lines(*args, **kwargs)

    monkeypatch.setattr(
        baseline_presence_edits_module,
        "_match_lines",
        record_match,
    )
    source_lines = [b"one\n", b"two\n"]
    working_lines = [b"prefix\n", b"two\n"]
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    fallback_chunks = try_apply_baseline_coordinate_edits(
        source_lines,
        working_lines,
        ownership,
        {2},
        [],
        spool_dir=spool_dir,
    )

    assert fallback_chunks is not None
    assert list(fallback_chunks) == working_lines
    assert observed_spools == [spool_dir]


def test_mapped_replacement_classifier_uses_invocation_spool(
    tmp_path,
    monkeypatch,
):
    """Mapped-unit overlap matching should use the caller's scratch path."""
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    observed_spools = []
    original_match_lines = validation_module.match_lines

    def record_match(*args, **kwargs):
        observed_spools.append(kwargs.get("spool_dir"))
        return original_match_lines(*args, **kwargs)

    monkeypatch.setattr(validation_module, "match_lines", record_match)
    source_lines = [b"head\n", b"new\n", b"tail\n"]
    working_lines = [b"head\n", b"local\n", b"new\n", b"tail\n"]
    deletion = AbsenceClaim(anchor_line=1, content_lines=[b"old\n"])
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [deletion],
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(2, 2, 2, 2),
            )
        ],
    )

    with match_lines(source_lines, working_lines) as mapping:
        fallback_chunks = try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            {2},
            [deletion],
            source_to_working_mapping=mapping,
            spool_dir=spool_dir,
        )

    assert fallback_chunks is not None
    assert list(fallback_chunks) == working_lines
    assert observed_spools == [spool_dir]


def test_mapped_replacement_recording_rolls_back_partial_unit() -> None:
    """A partly mapped unit must not leave target-line records behind."""
    with (
        MatcherWorkspace() as workspace,
        match_lines([b"one\n", b"two\n"], [b"one\n"]) as mapping,
    ):
        mapped_target_lines = workspace.record_vector(3, "Q")
        mapped_target_lines.append((7,))

        assert (
            baseline_replacement_edits_module._record_mapped_replacement_lines(
                ((1, 2),),
                mapping,
                mapped_target_lines,
            )
            is None
        )
        assert list(mapped_target_lines) == [(7,)]

        full_target_lines = workspace.record_vector(1, "Q")
        full_target_lines.append((7,))
        assert (
            baseline_replacement_edits_module._record_mapped_replacement_lines(
                ((1, 1),),
                mapping,
                full_target_lines,
            )
            is None
        )
        assert list(full_target_lines) == [(7,)]


def test_replacement_planner_closes_claimed_ranges_on_refusal(
    monkeypatch,
) -> None:
    """A refused replacement unit should release its parsed range storage."""
    with MatcherWorkspace() as workspace:
        claimed_ranges = workspace.record_vector(1, "QQ")
        monkeypatch.setattr(
            baseline_replacement_edits_module,
            "_collect_replacement_source_ranges",
            lambda *_args, **_kwargs: claimed_ranges,
        )
        plan = baseline_replacement_edits_module.BaselineEditPlan(
            workspace,
            edit_capacity=1,
            source_range_capacity=1,
        )
        deletion_edit_bounds = workspace.record_vector(
            1,
            "QQQQ",
            length=1,
        )
        replacement_source_ranges = workspace.record_vector(1, "QQ")
        mapped_target_lines = workspace.record_vector(1, "Q")

        assert not baseline_replacement_edits_module.plan_replacement_unit_edits(
            workspace,
            plan,
            1,
            [],
            [ReplacementUnit(["1"], [0])],
            [AbsenceClaim(anchor_line=None, content_lines=[b"old\n"])],
            deletion_edit_bounds,
            replacement_source_ranges,
            mapped_target_lines,
            None,
            max_resolution_choices=10,
            source_to_working_mapping=None,
            spool_dir=None,
        )
        assert claimed_ranges.closed


def merge_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
    *,
    source_to_working_mapping=None,
    resolution=None,
) -> bytes:
    """Return merged bytes through the buffer-returning production API."""
    with (
        LineBuffer.from_bytes(batch_source_content) as source_lines,
        LineBuffer.from_bytes(working_content) as working_lines,
        merge_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            source_to_working_mapping=source_to_working_mapping,
            resolution=resolution,
        ) as buffer,
    ):
        return buffer.to_bytes()


def discard_batch(
    batch_source_content: bytes,
    ownership: BatchOwnership,
    working_content: bytes,
    baseline_content: bytes,
) -> bytes:
    """Return discarded bytes through the buffer-returning production API."""
    with (
        LineBuffer.from_bytes(batch_source_content) as source_lines,
        LineBuffer.from_bytes(working_content) as working_lines,
        LineBuffer.from_bytes(baseline_content) as baseline_lines,
        discard_batch_from_line_sequences_as_buffer(
            source_lines,
            ownership,
            working_lines,
            baseline_lines,
        ) as buffer,
    ):
        return buffer.to_bytes()


def _two_line_replacement_origin_ownership() -> BatchOwnership:
    """Build the shared complete two-line replacement regression metadata."""
    reference = BaselineReference(
        after_line=1,
        after_content=b"head",
        before_line=4,
        before_content=b"tail",
        has_before_line=True,
    )
    return BatchOwnership.from_presence_lines(
        ["2-3"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old1\n", b"old2\n"],
                baseline_reference=reference,
            )
        ],
        baseline_references={2: reference, 3: reference},
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2-3"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(
                    old_start=2,
                    old_end=3,
                    new_start=2,
                    new_end=3,
                    baseline_reference=reference,
                ),
            )
        ],
    )


def _mapped_and_split_replacement_origin_ownership() -> BatchOwnership:
    """Build independent mapped-complete and unresolved-split units."""
    return BatchOwnership.from_presence_lines(
        ["2", "6"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"a-old1\n", b"a-old2\n"],
            ),
            AbsenceClaim(
                anchor_line=5,
                content_lines=[b"old2\n"],
            ),
        ],
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(2, 3, 2, 2),
            ),
            ReplacementUnit(
                presence_lines=["6"],
                deletion_indices=[1],
                origin=ReplacementUnitOrigin(5, 6, 5, 6),
            ),
        ],
    )


class TestMatchLines:
    """Tests for structural line alignment."""

    def test_line_mapping_uses_zero_filled_arrays(self):
        """Line mappings store one integer slot per line."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", b"line3\n"]

        mapping = match_lines(source, target)

        assert list(mapping.source_to_target) == [1, 0, 2]
        assert list(mapping.target_to_source) == [1, 3]
        assert mapping.get_target_line_from_source_line(2) is None

    def test_line_mapping_context_manager_closes_mapping(self):
        """Line mappings close their owned vector storage on context exit."""
        with match_lines([b"a\n"], [b"a\n"]) as mapping:
            assert mapping.get_target_line_from_source_line(1) == 1

        with pytest.raises(ValueError, match="line mapping is closed"):
            mapping.get_target_line_from_source_line(1)

    def test_line_mapping_exposes_mapped_line_pairs(self):
        """Mapped pair iteration exposes source-order correspondence."""
        with match_lines(
            [b"line1\n", b"line2\n", b"line3\n"],
            [b"line1\n", b"line3\n"],
        ) as mapping:
            assert list(mapping.mapped_line_pairs()) == [(1, 1), (3, 2)]

    def test_line_mapping_uses_explicit_anchor_between_repeated_lines(self):
        """A verified anchor divides otherwise ambiguous matching segments."""
        source = [b"head\n", b"new\n", b"\n", b"tail\n"]
        target = [b"head\n", b"\n", b"old\n", b"\n", b"tail\n"]

        with match_lines(source, target, anchor_pairs=((3, 2),)) as mapping:
            assert mapping.get_target_line_from_source_line(3) == 2
            assert mapping.get_source_line_from_target_line(4) is None

    def test_line_mapping_rejects_malformed_mixed_type_anchors(self):
        """Malformed anchors should fail with the public validation error."""
        with pytest.raises(
            ValueError,
            match="anchors must contain integer line numbers",
        ):
            match_lines(
                [b"one\n", b"two\n"],
                [b"one\n", b"two\n"],
                anchor_pairs=(("bad", 1), (2, 2)),
            )

    def test_identical_files(self):
        """Test alignment of identical files."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", b"line2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # All lines should be present and map 1:1
        assert mapping.is_source_line_present(1)
        assert mapping.is_source_line_present(2)
        assert mapping.is_source_line_present(3)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 2
        assert mapping.get_target_line_from_source_line(3) == 3

    def test_accepts_non_list_sequences(self, line_sequence):
        """match_lines only requires sized indexable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        target = line_sequence([b"line1\n", b"extra\n", b"line2\n", b"line3\n"])

        mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_acquires_line_buffer_lines(self):
        """LineBuffer inputs are matched through scoped line acquisition."""
        with (
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nline2\nline3\n"
            ) as source,
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nextra\nline2\nline3\n"
            ) as target,
        ):
            mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_acquires_normalized_line_buffer_lines(self):
        """Normalized LineBuffer inputs forward scoped acquisition."""
        with (
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\r\nline2\nline3\n"
            ) as source_buffer,
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nextra\nline2\nline3\n"
            ) as target_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            target = normalize_line_sequence_endings(target_buffer)

            mapping = match_lines(source, target)

        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 4
        assert mapping.get_source_line_from_target_line(2) is None

    def test_match_lines_does_not_materialize_guarded_lines(self):
        """Matching uses hashing and equality without bytes or slicing."""
        source = [
            _GuardedLine("start\n"),
            _GuardedLine("unique\n"),
            _GuardedLine("tail\n"),
        ]
        target = [
            _GuardedLine("start\n"),
            _GuardedLine("other\n"),
            _GuardedLine("unique\n"),
            _GuardedLine("tail\n"),
        ]

        with match_lines(source, target) as mapping:
            assert mapping.get_target_line_from_source_line(2) == 3

    def test_hash_collisions_do_not_conflate_unequal_lines(self):
        """Equal hashes route lookup but do not define line identity."""
        source = [
            _GuardedLine("start\n", 2),
            _GuardedLine("left\n", 1),
            _GuardedLine("right\n", 1),
            _GuardedLine("end\n", 3),
        ]
        target = [
            _GuardedLine("start\n", 2),
            _GuardedLine("right\n", 1),
            _GuardedLine("end\n", 3),
        ]

        with match_lines(source, target) as mapping:
            assert mapping.get_target_line_from_source_line(2) is None
            assert mapping.get_target_line_from_source_line(3) == 2

    def test_hash_collisions_coalesce_equal_repeated_lines(self):
        """Equal collided content is counted as repeated, not unique."""
        source = [
            _GuardedLine("start\n", 1),
            _GuardedLine("repeat\n", 1),
            _GuardedLine("repeat\n", 1),
            _GuardedLine("middle\n", 1),
            _GuardedLine("end\n", 1),
        ]
        target = [
            _GuardedLine("start\n", 1),
            _GuardedLine("other\n", 1),
            _GuardedLine("repeat\n", 1),
            _GuardedLine("changed\n", 1),
            _GuardedLine("end\n", 1),
        ]

        with match_lines(source, target) as mapping:
            assert mapping.get_target_line_from_source_line(1) == 1
            assert mapping.get_target_line_from_source_line(2) is None
            assert mapping.get_target_line_from_source_line(3) is None
            assert mapping.get_target_line_from_source_line(4) is None
            assert mapping.get_target_line_from_source_line(5) == 5

    def test_working_tree_additions(self):
        """Test alignment when working tree has extra lines."""
        source = [b"line1\n", b"line2\n", b"line3\n"]
        target = [b"line1\n", "extra1\n", b"line2\n", "extra2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # Source lines map to target positions
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(2) == 3
        assert mapping.get_target_line_from_source_line(3) == 5

        # Extra target lines map to None (not in source)
        assert mapping.get_source_line_from_target_line(2) is None
        assert mapping.get_source_line_from_target_line(4) is None

    def test_working_tree_deletions(self):
        """Test alignment when working tree is missing lines."""
        source = [b"line1\n", b"line2\n", b"line3\n", b"line4\n", b"line5\n"]
        target = [b"line1\n", b"line3\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Present lines map correctly
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(3) == 2
        assert mapping.get_target_line_from_source_line(5) == 3

        # Missing lines map to None
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(4) is None

    def test_replace_block_non_strict_no_match(self):
        """Test replace block where sub-matcher finds no matches."""
        source = [b"line1\n", "old2\n", "old3\n", b"line4\n"]
        target = [b"line1\n", "new2\n", "new3\n", b"line4\n"]

        mapping = match_lines(source, target)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(4) == 4

        # Replace block: sub-matcher sees these as completely different
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(3) is None

    def test_replace_block_non_strict_with_internal_match(self):
        """Test replace block where sub-matcher finds internal matches."""
        source = [b"line1\n", "A\n", "B\n", "C\n", b"line5\n"]
        target = [b"line1\n", "X\n", "B\n", "Y\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Equal blocks
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(5) == 5

        # Sub-matcher finds "B" matches within replace block
        assert mapping.get_target_line_from_source_line(3) == 3

        # A and C don't match
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_target_line_from_source_line(4) is None

    def test_replace_block_with_reordered_lines(self):
        """Test replace block with reordered lines shows sub-matcher behavior."""
        # Reordered replace block: A, B, C -> B, A, C.
        source = [b"line1\n", "A\n", "B\n", "C\n", b"line5\n"]
        target = [b"line1\n", "B\n", "A\n", "C\n", b"line5\n"]

        mapping = match_lines(source, target)

        # Equal blocks (unchanged)
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(5) == 5

        # Structural anchors preserve source and target order:
        # - A (source line 2) maps to A (target line 3)
        # - B gets treated as delete + reinsert (maps to None)
        # - C (source line 4) maps to C (target line 4)
        assert mapping.get_target_line_from_source_line(2) == 3  # A matches
        assert mapping.get_target_line_from_source_line(3) is None  # B seen as moved
        assert mapping.get_target_line_from_source_line(4) == 4  # C matches

        # Moved lines stay unmapped unless they fit the ordered anchor sequence.

    def test_replace_block_strict(self):
        """Test alignment with replace block in strict mode."""
        source = [b"line1\n", "old2\n", b"line3\n"]
        target = [b"line1\n", "new2\n", b"line3\n"]

        mapping = match_lines(source, target)

        # Equal blocks map
        assert mapping.get_target_line_from_source_line(1) == 1
        assert mapping.get_target_line_from_source_line(3) == 3

        # Replace block: source line maps to None in strict mode
        assert mapping.get_target_line_from_source_line(2) is None
        assert mapping.get_source_line_from_target_line(2) is None


class TestMergeLineSequences:
    """Tests for merge helpers accepting non-list line sequences."""

    def test_constraint_helpers_accept_non_list_sequences(self, line_sequence):
        """Read-only merge helpers only require sized indexable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])
        mapping = match_lines(source, working)

        check_structural_validity(
            mapping,
            {2},
            [],
            source,
            working,
        )
        entries = satisfy_constraints(
            source,
            working,
            {2},
            [],
            source_to_working_mapping=mapping,
        )

        assert isinstance(entries, RealizedEntries)
        assert b"".join(entry.content for entry in entries) == b"line1\nline2\nline3\n"

    def test_supplied_mapping_preserves_verified_deletion_anchor(self):
        """A reusable plain mapping must not bypass saved removal anchors."""
        source = [b"head\n", b"new\n", b"\n", b"tail\n"]
        working = [b"head\n", b"\n", b"old\n", b"\n", b"tail\n"]
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [
                AbsenceClaim(
                    anchor_line=3,
                    content_lines=[b"old\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"\n",
                        before_line=4,
                        before_content=b"\n",
                        has_before_line=True,
                    ),
                )
            ],
        )

        with match_lines(source, working) as mapping:
            assert mapping.get_target_line_from_source_line(3) == 4
            result = merge_batch(
                b"".join(source),
                ownership,
                b"".join(working),
                source_to_working_mapping=mapping,
            )

        assert result == b"head\nnew\n\n\ntail\n"

    def test_discard_entry_builder_accepts_non_list_sequences(self, line_sequence):
        """Discard entry construction only requires sized iterable line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])
        mapping = match_lines(source, working)

        entries = _build_realized_entries_for_discard(source, working, mapping)

        assert isinstance(entries, RealizedEntries)
        assert [entry.content for entry in entries] == [b"line1\n", b"line3\n"]
        assert [entry.source_line for entry in entries] == [1, 3]

    def test_realized_entry_content_chunks_avoids_entry_views(self):
        """Realized content streaming should not index compact entry storage."""
        entries = _IndexGuardedRealizedEntries()
        entries.append(b"line1\n")
        entries.append(b"line2\n")

        assert list(realized_entry_content_chunks(entries)) == [
            b"line1\n",
            b"line2\n",
        ]

    def test_realized_entry_pending_read_does_not_flush_or_block_coalescing(self):
        """Reading a pending provenance run keeps it available for coalescing."""
        lines = [b"line1\n", b"line2\n"]
        entries = RealizedEntries()
        entries.append_line_from(lines, 0, source_line=1, target_line=1)

        assert entries.source_line_at(0) == 1

        entries.append_line_from(lines, 1, source_line=2, target_line=2)

        runs = list(entries.provenance_runs())
        assert len(runs) == 1
        assert (runs[0].dest_start, runs[0].dest_end) == (0, 2)

    def test_realized_entry_copy_slice_clips_partial_runs(self):
        """copy_slice_from adjusts first and last clipped run provenance."""
        lines = [b"one\n", b"two\n", b"three\n", b"four\n", b"five\n"]
        entries = RealizedEntries()
        entries.append_line_range_from(
            lines,
            0,
            len(lines),
            source_line_start=1,
            target_line_start=10,
        )

        result = RealizedEntries()
        result.copy_slice_from(entries, 1, 4)

        assert list(result.content_chunks()) == [b"two\n", b"three\n", b"four\n"]
        assert [result.source_line_at(index) for index in range(len(result))] == [2, 3, 4]
        assert [result.target_line_at(index) for index in range(len(result))] == [11, 12, 13]
        runs = list(result.provenance_runs())
        assert len(runs) == 1
        assert (runs[0].dest_start, runs[0].dest_end) == (0, 3)
        assert (runs[0].source_start, runs[0].target_start) == (2, 11)

    def test_realized_entry_adjacent_contiguous_runs_coalesce(self):
        """Adjacent compatible provenance ranges stay in one run."""
        lines = [b"one\n", b"two\n", b"three\n", b"four\n"]
        entries = RealizedEntries()

        entries.append_line_range_from(
            lines,
            0,
            2,
            source_line_start=1,
            target_line_start=20,
        )
        entries.append_line_range_from(
            lines,
            2,
            4,
            source_line_start=3,
            target_line_start=22,
        )

        runs = list(entries.provenance_runs())
        assert len(runs) == 1
        assert (runs[0].dest_start, runs[0].dest_end) == (0, 4)
        assert (runs[0].source_start, runs[0].target_start) == (1, 20)

    def test_realized_entry_claimed_changes_split_runs(self):
        """Claimed state changes are provenance run boundaries."""
        lines = [b"one\n", b"two\n", b"three\n", b"four\n"]
        entries = RealizedEntries()

        entries.append_line_range_from(
            lines,
            0,
            2,
            source_line_start=1,
            target_line_start=1,
            is_claimed=False,
        )
        entries.append_line_range_from(
            lines,
            2,
            4,
            source_line_start=3,
            target_line_start=3,
            is_claimed=True,
        )

        runs = list(entries.provenance_runs())
        assert len(runs) == 2
        assert [run.is_claimed for run in runs] == [False, True]
        assert [(run.dest_start, run.dest_end) for run in runs] == [(0, 2), (2, 4)]

    def test_realized_entry_source_and_target_lookup_across_none_runs(self):
        """Random lookup works across None and numbered provenance runs."""
        lines = [b"one\n", b"two\n", b"three\n", b"four\n"]
        entries = RealizedEntries()
        entries.append_line_range_from(
            lines,
            0,
            2,
            source_line_start=None,
            target_line_start=10,
        )
        entries.append_line_range_from(
            lines,
            2,
            4,
            source_line_start=30,
            target_line_start=None,
        )

        assert [entries.source_line_at(index) for index in range(4)] == [None, None, 30, 31]
        assert [entries.target_line_at(index) for index in range(4)] == [10, 11, None, None]

    def test_closed_realized_entries_reject_access(self):
        """Public realized-entry APIs reject use after close."""
        donor = RealizedEntries([RealizedEntry(b"donor\n", source_line=1)])
        entries = RealizedEntries([RealizedEntry(b"line\n", source_line=1)])
        entries.close()

        accessors = [
            lambda: len(entries),
            lambda: entries[0],
            lambda: entries.source_line_at(0),
            lambda: entries.target_line_at(0),
            lambda: entries.is_claimed_at(0),
            lambda: entries.content_at(0),
            lambda: list(entries.content_chunks()),
            lambda: entries.slice(0, 1),
            lambda: entries.without_range(0, 1),
            lambda: entries.insert_entries(0, donor),
            lambda: entries.append(b"new\n"),
            lambda: entries.append_line_from([b"new\n"], 0),
            lambda: entries.append_line_range_from([b"new\n"], 0, 1),
            lambda: entries.append_entry(RealizedEntry(b"new\n", source_line=None)),
            lambda: entries.append_from(donor, 0),
            lambda: entries.copy_slice_from(donor, 0, 1),
        ]

        for accessor in accessors:
            with pytest.raises(ValueError, match="closed"):
                accessor()

    def test_realized_entries_context_manager_closes_provenance(self):
        """Context manager cleanup closes mapped provenance resources."""
        with RealizedEntries() as entries:
            entries.append(b"line\n", source_line=1)
            provenance = entries._provenance

        assert entries.closed
        assert provenance.closed

    def test_realized_entry_slice_without_range_and_insert_preserve_provenance(self):
        """Structural copy operations preserve content and provenance."""
        lines = [b"one\n", b"two\n", b"three\n", b"four\n"]
        entries = RealizedEntries()
        entries.append_line_range_from(
            lines,
            0,
            4,
            source_line_start=1,
            target_line_start=11,
        )
        inserted = RealizedEntries()
        inserted.append_line_range_from(
            [b"inserted\n"],
            0,
            1,
            source_line_start=None,
            target_line_start=None,
            is_claimed=True,
        )

        sliced = entries.slice(1, 3)
        without = entries.without_range(1, 3)
        combined = entries.insert_entries(2, inserted)

        assert list(sliced.content_chunks()) == [b"two\n", b"three\n"]
        assert [sliced.source_line_at(index) for index in range(len(sliced))] == [2, 3]
        assert [sliced.target_line_at(index) for index in range(len(sliced))] == [12, 13]
        assert list(without.content_chunks()) == [b"one\n", b"four\n"]
        assert [without.source_line_at(index) for index in range(len(without))] == [1, 4]
        assert list(combined.content_chunks()) == [
            b"one\n",
            b"two\n",
            b"inserted\n",
            b"three\n",
            b"four\n",
        ]
        assert [combined.source_line_at(index) for index in range(len(combined))] == [
            1,
            2,
            None,
            3,
            4,
        ]
        assert combined.is_claimed_at(2) is True

    def test_realized_entry_getitem_reconstructs_entry(self):
        """__getitem__ returns the expected RealizedEntry view."""
        entries = RealizedEntries()
        entries.append(
            b"line\n",
            source_line=7,
            target_line=9,
            is_claimed=True,
        )

        assert entries[0] == RealizedEntry(
            content=b"line\n",
            source_line=7,
            target_line=9,
            is_claimed=True,
        )

    def test_large_contiguous_discard_builder_uses_small_run_count(self):
        """Large contiguous source/target mappings collapse to one provenance run."""
        lines = [f"line {index}\n".encode() for index in range(1000)]
        with match_lines(lines, lines) as mapping:
            entries = _build_realized_entries_for_discard(lines, lines, mapping)

        assert len(entries) == 1000
        assert len(entries._provenance) == 1
        assert entries.source_line_at(999) == 1000
        entries.close()

    def test_large_contiguous_merge_uses_small_run_count(self):
        """Large contiguous merge realization collapses to one provenance run."""
        lines = [f"line {index}\n".encode() for index in range(1000)]

        entries = satisfy_constraints(lines, lines, set(), [])

        assert len(entries) == 1000
        assert len(entries._provenance) == 1
        assert entries.target_line_at(999) == 1000
        entries.close()

    def test_reverse_presence_constraints_scans_realized_runs(self):
        """Discard restore scans realized runs instead of per-line lookups."""
        baseline = [b"one\n", b"old\n", b"three\n"]
        source = [b"one\n", b"new\n", b"three\n"]
        correspondence = build_baseline_correspondence(baseline, source)
        entries = _SourceLookupGuardedRealizedEntries()
        entries.append_line_range_from(
            source,
            0,
            3,
            source_line_start=1,
            target_line_start=1,
        )

        try:
            result = reverse_presence_constraints(entries, {2}, correspondence)
        finally:
            entries.close()

        try:
            assert list(result.content_chunks()) == baseline
        finally:
            result.close()

    def test_reverse_presence_refuses_mixed_applied_hunk_expansion(self):
        """Applied rollback must not expand a partly coupled baseline hunk."""
        baseline = [b"head\n", b"old-a\n", b"old-b\n", b"tail\n"]
        source = [b"head\n", b"new-a\n", b"new-b\n", b"tail\n"]
        correspondence = build_baseline_correspondence(baseline, source)
        assert correspondence.get_region_for_source_line(2).kind == (
            RegionKind.REPLACE_BY_HUNK
        )
        entries = RealizedEntries()
        entries.append_line_range_from(
            source,
            0,
            len(source),
            source_line_start=1,
            target_line_start=1,
        )

        try:
            with pytest.raises(MergeError, match="lacking an independent old side"):
                reverse_presence_constraints(
                    entries,
                    {2, 3},
                    correspondence,
                    trusted_insertion_lines={2},
                    separately_restored_ranges=((2, 2),),
                )
        finally:
            entries.close()

    def test_reverse_presence_closes_partial_result_on_failure(
        self,
        monkeypatch,
    ):
        """A refused rollback must release its partially built output."""
        created_results = []

        class TrackingRealizedEntries(RealizedEntries):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created_results.append(self)

        monkeypatch.setattr(
            discard_reversal_module,
            "RealizedEntries",
            TrackingRealizedEntries,
        )
        correspondence = build_baseline_correspondence([], [])
        entries = [RealizedEntry(b"owned\n", source_line=1)]

        with pytest.raises(MergeError, match="no baseline restoration region"):
            reverse_presence_constraints(entries, {1}, correspondence)

        assert len(created_results) == 1
        assert created_results[0].closed

    def test_reverse_presence_defers_fully_coupled_applied_hunk(self):
        """Every coupled line may defer old-side restoration to its claim."""
        baseline = [b"head\n", b"old-a\n", b"old-b\n", b"tail\n"]
        source = [b"head\n", b"new-a\n", b"new-b\n", b"tail\n"]
        correspondence = build_baseline_correspondence(baseline, source)
        entries = RealizedEntries()
        entries.append_line_range_from(
            source,
            0,
            len(source),
            source_line_start=1,
            target_line_start=1,
        )

        try:
            result = reverse_presence_constraints(
                entries,
                {2, 3},
                correspondence,
                trusted_insertion_lines={2},
                separately_restored_ranges=((2, 3),),
            )
        finally:
            entries.close()

        try:
            assert list(result.content_chunks()) == [b"head\n", b"tail\n"]
        finally:
            result.close()

    def test_baseline_correspondence_accepts_non_list_sequences(self, line_sequence):
        """Baseline correspondence accepts sized sliceable line sequences."""
        baseline = line_sequence([b"line1\n", b"old\n", b"line3\n"])
        source = line_sequence([b"line1\n", b"new\n", b"line3\n"])

        correspondence = build_baseline_correspondence(baseline, source)
        region = correspondence.get_region_for_source_line(2)

        assert region is not None
        assert region.kind == RegionKind.REPLACE_BY_HUNK
        assert tuple(region.baseline_lines) == (b"old\n",)
        assert not isinstance(region.baseline_lines, list)

    def test_baseline_correspondence_looks_up_adjacent_ranges(self):
        """Baseline correspondence maps source lines through region bounds."""
        baseline = [b"line1\n", b"line3\n"]
        source = [b"line1\n", b"line2\n", b"line3\n"]

        correspondence = build_baseline_correspondence(baseline, source)

        assert correspondence.get_region_for_source_line(0) is None
        assert correspondence.get_region_for_source_line(1).kind == RegionKind.EQUAL
        assert correspondence.get_region_for_source_line(2).kind == RegionKind.INSERT
        assert correspondence.get_region_for_source_line(3).kind == RegionKind.EQUAL
        assert correspondence.get_region_for_source_line(4) is None
        assert not hasattr(correspondence, "line_to_region")

    def test_can_merge_accepts_non_list_sequences(self, line_sequence):
        """Mergeability probes accept indexed line sequences."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\n", b"line3\n"])

        assert can_merge_batch_from_line_sequences(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
        ) is True

    def test_merge_from_line_sequences_can_return_buffer(self, line_sequence):
        """Merge can return a buffer without materializing through the bytes API."""
        source = line_sequence([b"line1\n", b"line2\n", b"line3\n"])
        working = line_sequence([b"line1\r\n", b"line3\r\n"])

        with merge_batch_from_line_sequences_as_buffer(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
        ) as result:
            assert result.to_bytes() == b"line1\r\nline2\r\nline3\r\n"

    def test_discard_from_line_sequences_can_return_buffer(self, line_sequence):
        """Discard can return a buffer without materializing through the bytes API."""
        baseline = line_sequence([b"line1\n", b"old\n", b"line3\n"])
        source = line_sequence([b"line1\n", b"new\n", b"line3\n"])
        working = line_sequence([b"line1\r\n", b"new\r\n", b"line3\r\n"])

        with discard_batch_from_line_sequences_as_buffer(
            source,
            BatchOwnership.from_presence_lines(["2"], []),
            working,
            baseline,
        ) as result:
            assert result.to_bytes() == b"line1\r\nold\r\nline3\r\n"

    def test_merge_chunks_acquire_normalized_line_buffer_lines(self):
        """Merge realization uses scoped normalized line acquisition."""
        with (
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nline2\nline3\n"
            ) as source_buffer,
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nline3\n"
            ) as working_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            working = normalize_line_sequence_endings(working_buffer)

            result = b"".join(
                _merge_batch_line_chunks(
                    source,
                    BatchOwnership.from_presence_lines(["2"], []),
                    working,
                )
            )

        assert result == b"line1\nline2\nline3\n"

    def test_discard_chunks_acquire_normalized_line_buffer_lines(self):
        """Discard realization uses scoped normalized line acquisition."""
        with (
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nnew\nline3\n"
            ) as source_buffer,
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nnew\nline3\n"
            ) as working_buffer,
            _IndexGuardedLineBuffer.from_bytes(
                b"line1\nold\nline3\n"
            ) as baseline_buffer,
        ):
            source = normalize_line_sequence_endings(source_buffer)
            working = normalize_line_sequence_endings(working_buffer)
            baseline = normalize_line_sequence_endings(baseline_buffer)

            result = b"".join(
                _discard_batch_line_chunks(
                    source,
                    BatchOwnership.from_presence_lines(["2"], []),
                    working,
                    baseline,
                )
            )

        assert result == b"line1\nold\nline3\n"


class TestMergeBatch:
    """Tests for batch merge algorithm."""

    def test_merge_identical_files(self):
        """Test merge when files are identical (no-op)."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        result = merge_batch(source, BatchOwnership([], []), working)
        assert result == working

    def test_merge_add_missing_claimed_line(self):
        """Test merge that adds a missing claimed line."""
        source = b"line1\nline2\nline3\nline4\nline5\n"
        working = b"line1\nline3\nline5\n"  # Missing lines 2, 4
        claimed = ["2"]  # Claim line 2

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert line2 between line1 and line3
        assert result == b"line1\nline2\nline3\nline5\n"

    def test_merge_preserves_target_crlf_endings(self):
        """Merge should not turn a CRLF target into LF bytes."""
        source = b"line1\r\nline2\r\nline3\r\n"
        working = b"line1\r\nline3\r\n"
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        assert result == b"line1\r\nline2\r\nline3\r\n"

    def test_merge_preserves_working_tree_extras(self):
        """Test that merge preserves working tree extras."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nextra1\nline2\nextra2\nline3\n"
        claimed = ["2"]  # Claim line2

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should preserve extras
        assert result == working

    def test_baseline_referenced_presence_is_noop_when_already_present(self):
        """Baseline-coordinate insertion fallback should satisfy, not duplicate."""
        source = b"base\nfoo\nbar\n"
        working = b"base\nfoo\nbar\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [],
            baseline_references={
                2: BaselineReference(
                    after_line=1,
                    after_content=b"base",
                    before_line=2,
                    before_content=b"bar",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == working

    def test_baseline_referenced_presence_inserts_when_missing(self):
        """Baseline-coordinate insertion fallback handles baseline targets."""
        source = b"base\nfoo\nbar\n"
        working = b"base\nbar\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [],
            baseline_references={
                2: BaselineReference(
                    after_line=1,
                    after_content=b"base",
                    before_line=2,
                    before_content=b"bar",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == source

    def test_stale_baseline_reference_does_not_silence_presence_ambiguity(self):
        """An unresolved coordinate cannot permit a silent variant interleave."""
        source = b"head\nsaved variant\ntail\n"
        working = b"head\nlive variant\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            baseline_references={
                2: BaselineReference(
                    after_line=1,
                    after_content=b"stale-head",
                    before_line=3,
                    before_content=b"tail",
                    has_before_line=True,
                )
            },
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_empty_absence_does_not_silence_presence_ambiguity(self):
        """A no-op deletion must not become an unmapped trusted anchor."""
        source = b"head\nsaved variant\ntail\n"
        working = b"head\nlive variant\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [AbsenceClaim(anchor_line=2, content_lines=[])],
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_unmapped_unrelated_absence_does_not_silence_presence_ambiguity(self):
        """A missing deletion anchor must not bypass presence placement checks."""
        source = b"head\nsaved variant\ntail\n"
        working = b"head\nlive variant\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [AbsenceClaim(anchor_line=2, content_lines=[b"unrelated\n"])],
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_empty_absence_is_not_trusted_during_constraint_realization(self):
        """Direct realization must apply the same empty-anchor filtering."""
        selected = LineRanges.from_specs(["2"])

        with pytest.raises(MergeError):
            satisfy_constraints(
                [b"head\n", b"saved variant\n", b"tail\n"],
                [b"head\n", b"live variant\n", b"tail\n"],
                selected,
                [AbsenceClaim(anchor_line=2, content_lines=[])],
                require_distinctive_context=True,
                distinctive_context_lines=selected,
            )

    def test_baseline_referenced_noncontiguous_presence_is_noop_when_source_matches(self):
        """Already-satisfied additions may be interleaved with unclaimed source lines."""
        source = b"line1\nline2\nline3\nline4\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )

        result = merge_batch(source, ownership, source)

        assert result == source

    def test_baseline_referenced_noncontiguous_presence_inserts_subset_when_missing(self):
        """Baseline-coordinate insertion can stage selected additions without siblings."""
        source = b"line1\nline2\nline3\nline4\n"
        working = b"line1\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == b"line1\nline2\nline4\n"

    def test_legacy_eof_reference_defers_after_target_grows(self):
        """An after-only EOF reference must not precede mapped target additions."""
        source = b"base\nfirst\n\n"
        working = b"base\nfirst\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [],
            baseline_references={
                3: BaselineReference(
                    after_line=1,
                    after_content=b"base",
                    before_line=None,
                    has_before_line=False,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == source

    def test_baseline_referenced_fallback_yields_line_chunks(self):
        """Baseline-coordinate fallback returns line content chunks."""
        source_lines = [b"line1\n", b"line2\n", b"line3\n", b"line4\n"]
        working_lines = [b"line1\n"]
        ownership = BatchOwnership.from_presence_lines(
            ["2,4"],
            [],
            baseline_references={
                line: BaselineReference(
                    after_line=1,
                    after_content=b"line1",
                    before_line=None,
                    has_before_line=False,
                )
                for line in (2, 4)
            },
        )
        fallback_chunks = try_apply_baseline_coordinate_edits(
            source_lines,
            working_lines,
            ownership,
            {2, 4},
            [],
        )

        assert fallback_chunks is not None
        assert list(fallback_chunks) == [b"line1\n", b"line2\n", b"line4\n"]

    def test_merge_with_deletion_suppresses_content(self):
        """Test that deletion constraints suppress matching content."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nline3\n"

        # Create absence claim to suppress "unwanted"
        deletions = [AbsenceClaim(anchor_line=None, content_lines=[b"unwanted\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the unwanted line
        assert result == b"line1\nline2\nline3\n"

    def test_merge_with_deletion_accepts_non_list_content_lines(self, line_sequence):
        """Deletion suppression only requires indexed content lines."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nline3\n"
        deletions = [
            AbsenceClaim(
                anchor_line=None,
                content_lines=line_sequence([b"unwanted\n"]),
            ),
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        assert result == b"line1\nline2\nline3\n"

    def test_baseline_referenced_absence_suppresses_when_source_anchor_missing(self):
        """Absence-only fallback should use exact baseline coordinates."""
        source = b"line1\nnew context\nline3\n"
        working = b"line1\nold value\nline3\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"line1\n",
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"line1\nline3\n"

    def test_identical_shifted_source_keeps_pending_structural_removal(self):
        """A stale baseline coordinate must not hide a source-anchored removal."""
        source = b"prefix\nA\nold value\nB\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"A",
                        before_line=3,
                        before_content=b"B",
                        has_before_line=True,
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, source)

        assert result == b"prefix\nA\nB\n"

    def test_baseline_referenced_absence_rejects_unidentified_numeric_anchor(self):
        """A line number alone cannot prove which duplicate occurrence is owned."""
        source = b"line1\nnew context\nline3\n"
        working = b"line1\nold value\nline3\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_baseline_referenced_absence_is_noop_when_already_absent(self):
        """Already-satisfied absence constraints should not block a round trip."""
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=1,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        result = merge_batch(b"", ownership, b"")

        assert result == b""

    def test_baseline_referenced_replacement_is_noop_when_source_matches(self):
        """Applying replacement ownership back to its own source is a no-op."""
        source = b"A\nsame\n"
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            [
                AbsenceClaim(
                    anchor_line=None,
                    content_lines=[b"same\n"],
                    baseline_reference=BaselineReference(
                        after_line=None,
                        before_line=2,
                        before_content=b"same",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                1: BaselineReference(
                    after_line=None,
                    before_line=2,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["1"],
                    deletion_indices=[0],
                )
            ],
        )

        result = merge_batch(source, ownership, source)

        assert result == source

    def test_split_replacement_origin_places_subunit_inside_parent_boundary(self):
        """Parent replacement context constrains exact split-unit reapply."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold1\nold2\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"old1",
                        before_line=4,
                        before_content=b"tail",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                3: BaselineReference(
                    after_line=2,
                    after_content=b"old1",
                    before_line=4,
                    before_content=b"tail",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"head\nold1\nnew2\ntail\n"

    def test_full_replacement_origin_allows_structural_replay_after_prefix(self):
        """A complete origin unit does not require a split-parent boundary."""
        source = b"landed\nhead\nnew\ntail\n"
        working = b"landed\nhead\nold\nlocal\ntail\n"
        replacement_reference = BaselineReference(
            after_line=1,
            after_content=b"head",
            before_line=3,
            before_content=b"tail",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old\n"],
                    baseline_reference=replacement_reference,
                )
            ],
            baseline_references={3: replacement_reference},
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=2,
                        new_start=2,
                        new_end=2,
                        baseline_reference=replacement_reference,
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"landed\nhead\nnew\nlocal\ntail\n"

    @pytest.mark.parametrize(
        "working",
        [
            b"head\nnew1\nold1\nold2\ntail\n",
            b"head\nold1\nold2\ntail\nnew1\n",
        ],
        ids=["structural", "coordinate-fast-path"],
    )
    def test_full_replacement_origin_refuses_hybrid_new_side(
        self,
        working,
    ):
        """Complete origin counts do not waive partial live realization."""
        source = b"head\nnew1\nnew2\ntail\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_full_replacement_origin_skips_mapped_coordinate_replay(self):
        """Mapped new lines force structural replay instead of duplication."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold1\nold2\ntail\nnew1\nnew2\n"
        ownership = _two_line_replacement_origin_ownership()

        assert merge_batch(source, ownership, working) == (
            b"head\ntail\nnew1\nnew2\n"
        )

        with pytest.raises(
            MergeError,
            match="Selected merge resolution is no longer valid",
        ):
            merge_batch(
                source,
                ownership,
                working,
                resolution=MergeResolution({
                    AMBIGUITY_KEY: (
                        CoordinateStrategyChoice.RECORDED_COORDINATES.value
                    ),
                }),
            )

    def test_mapped_full_replacement_origin_refuses_partial_old_side(self):
        """Mapped replacement content cannot strand an old-side suffix."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold2\ntail\nnew1\nnew2\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_safe_mapped_replacement_unit_allows_other_split_review(self):
        """An absent mapped old side does not block an independent review."""
        source = b"a-head\na-new\na-tail\nhead\nnew1\nnew2\ntail\n"
        working = b"a-head\na-new\na-tail\nhead\nold2\ntail\n"
        ownership = _mapped_and_split_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED
        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "replace target lines 5 with source lines 6",
        ]
        assert merge_batch(
            source,
            ownership,
            working,
            resolution=candidate_set.candidates[0].resolution,
        ) == b"a-head\na-new\na-tail\nhead\nnew2\ntail\n"

    def test_mapped_full_replacement_origin_refuses_fragmented_old_side(self):
        """Mapped replacement content cannot hide fragmented old-side lines."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold1\nlocal\nold2\ntail\nnew1\nnew2\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    @pytest.mark.parametrize(
        "working",
        [
            b"head\nold1\nold2\nnew1\nnew2\nold2\ntail\n",
            b"head\nold1\nold2\nnew1\nnew2\nold1\nold2\ntail\n",
        ],
        ids=["full-plus-partial", "two-full-copies"],
    )
    def test_mapped_replacement_refuses_old_content_after_full_old_side(
        self,
        working,
    ):
        """Removing one full old side cannot strand another old fragment."""
        source = b"head\nnew1\nnew2\ntail\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_mapped_full_replacement_origin_preserves_unrelated_local_content(
        self,
    ):
        """An absent old side leaves unrelated content in its gap untouched."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nlocal\ntail\nnew1\nnew2\n"
        ownership = _two_line_replacement_origin_ownership()

        assert merge_batch(source, ownership, working) == working

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert (
            candidate_set.outcome
            is MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED
        )

    @pytest.mark.parametrize(
        "working",
        [
            b"head\nnew1\nnew2\nold2\ntail\n",
            b"head\nnew1\nnew2\nold1\nlocal\nold2\ntail\n",
        ],
        ids=["partial", "fragmented"],
    )
    def test_mapped_full_replacement_origin_refuses_old_side_after_new_side(
        self,
        working,
    ):
        """Partial old content after mapped claimed lines is still unsafe."""
        source = b"head\nnew1\nnew2\ntail\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_mapped_full_replacement_origin_removes_full_old_side_after_new_side(
        self,
    ):
        """A complete old side remains removable after mapped claimed lines."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nnew1\nnew2\nold1\nold2\ntail\n"
        ownership = _two_line_replacement_origin_ownership()

        assert merge_batch(source, ownership, working) == source

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert (
            candidate_set.outcome
            is MergeCandidateSetOutcome.ORDINARY_MERGE_SUCCEEDED
        )

    def test_mapped_split_replacement_origin_refuses_partial_old_side(self):
        """A mapped split unit cannot strand part of its selected old side."""
        source = b"head\nnew1\nnew2\nnew3\ntail\n"
        working = b"head\nnew1\nold3\ntail\nnew2\nnew3\n"
        replacement_reference = BaselineReference(
            after_line=2,
            after_content=b"old1",
            before_line=5,
            before_content=b"tail",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["3-4"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n", b"old3\n"],
                    baseline_reference=replacement_reference,
                )
            ],
            baseline_references={
                3: replacement_reference,
                4: replacement_reference,
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3-4"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=4,
                        new_start=2,
                        new_end=4,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=5,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_mapped_replacement_unit_partial_old_side_blocks_other_review(self):
        """One unsafe mapped unit vetoes review for an independent split."""
        source = b"a-head\na-new\na-tail\nhead\nnew1\nnew2\ntail\n"
        working = b"a-head\na-old2\na-new\na-tail\nhead\nold2\ntail\n"
        ownership = _mapped_and_split_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED
        assert candidate_set.candidates == ()

    def test_reviewed_unit_rejects_unrelated_mixed_replacement_replay(self):
        """A reviewed unit cannot bypass a mixed independent replacement."""
        source = (
            b"a-head\na-new1\na-new2\na-tail\n"
            b"head\nnew1\nnew2\ntail\n"
        )
        replacement_reference = BaselineReference(
            after_line=1,
            after_content=b"a-head\n",
        )
        ownership = BatchOwnership.from_presence_lines(
            ["2-3", "7"],
            [
                AbsenceClaim(
                    anchor_line=1,
                    content_lines=[b"a-old1\n", b"a-old2\n"],
                    baseline_reference=replacement_reference,
                ),
                AbsenceClaim(anchor_line=6, content_lines=[b"old2\n"]),
            ],
            baseline_references={
                2: replacement_reference,
                3: replacement_reference,
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["2-3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        2,
                        3,
                        2,
                        3,
                        replacement_reference,
                    ),
                ),
                ReplacementUnit(
                    presence_lines=["7"],
                    deletion_indices=[1],
                    origin=ReplacementUnitOrigin(6, 7, 6, 7),
                ),
            ],
        )
        review_target = (
            b"a-head\na-new1\na-new2\na-tail\nhead\nold2\ntail\n"
        )
        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            review_target.splitlines(keepends=True),
            max_candidates=10,
        )
        assert candidate_set.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED

        replayed = (
            b"a-head\na-old1\na-old2\na-new1\na-tail\n"
            b"head\nold2\ntail\n"
        )
        with pytest.raises(
            MergeError,
            match="Selected merge resolution is no longer valid",
        ):
            merge_batch(
                source,
                ownership,
                replayed,
                resolution=candidate_set.candidates[0].resolution,
            )

    @pytest.mark.parametrize(
        "replayed",
        [
            b"head\nnew1\nold1\nold2\ntail\n",
            b"head\nnew1\nnew2\ntail\nold1\nold2\n",
        ],
        ids=["partly-realized", "fully-realized"],
    )
    def test_full_replacement_origin_rejects_stale_realized_resolution(
        self,
        replayed,
    ):
        """Reviewed placement cannot duplicate an already realized new side."""
        source = b"head\nnew1\nnew2\ntail\n"
        displaced = b"head\nlocal\ntail\nold1\nold2\n"
        ownership = _two_line_replacement_origin_ownership()
        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            displaced.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED
        assert len(candidate_set.candidates) == 1

        with pytest.raises(
            MergeError,
            match="Selected merge resolution is no longer valid",
        ):
            merge_batch(
                source,
                ownership,
                replayed,
                resolution=candidate_set.candidates[0].resolution,
            )

    def test_split_replacement_origin_rejects_stale_realized_resolution(self):
        """Mapped split content cannot be duplicated by coordinate replay."""
        source = b"head\nnew1\nnew2\ntail\n"
        displaced = b"head\nold2\ntail\n"
        replayed = b"head\nnew2\nold2\ntail\n"
        mapped_elsewhere = b"head\nnew1\nold2\ntail\nnew2\n"
        replacement_reference = BaselineReference(
            after_line=2,
            after_content=b"old1",
            before_line=4,
            before_content=b"tail",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n"],
                    baseline_reference=replacement_reference,
                )
            ],
            baseline_references={3: replacement_reference},
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        assert merge_batch(source, ownership, mapped_elsewhere) == (
            b"head\nnew1\ntail\nnew2\n"
        )
        with pytest.raises(
            MergeError,
            match="Selected merge resolution is no longer valid",
        ):
            merge_batch(
                source,
                ownership,
                mapped_elsewhere,
                resolution=MergeResolution({
                    AMBIGUITY_KEY: (
                        CoordinateStrategyChoice.RECORDED_COORDINATES.value
                    ),
                }),
            )

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            displaced.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.outcome is MergeCandidateSetOutcome.REVIEW_REQUIRED
        assert len(candidate_set.candidates) == 1

        with pytest.raises(
            MergeError,
            match="Selected merge resolution is no longer valid",
        ):
            merge_batch(
                source,
                ownership,
                replayed,
                resolution=candidate_set.candidates[0].resolution,
            )

    def test_full_replacement_origin_refuses_partial_old_side(self):
        """A complete missing new side cannot replace a partial old side."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold2\ntail\n"
        ownership = _two_line_replacement_origin_ownership()

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

    def test_split_replacement_uses_baseline_offset_after_source_prefix(self):
        """Retained source-only lines must not shift a split replacement."""
        source = b"saved\nhead\nnew1\nnew2\ntail\n"
        working = b"head\nsame\nsame\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"same\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"head",
                        before_line=3,
                        before_content=b"same",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                3: BaselineReference(
                    after_line=1,
                    after_content=b"head",
                    before_line=3,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"head\nnew1\nsame\ntail\n"

    def test_split_replacement_origin_places_subunit_in_hybrid_parent(self):
        """Split units should reapply where unselected sibling lines remain new."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nnew1\nold2\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"old1",
                        before_line=4,
                        before_content=b"tail",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                3: BaselineReference(
                    after_line=2,
                    after_content=b"old1",
                    before_line=4,
                    before_content=b"tail",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        result = merge_batch(source, ownership, working)

        assert result == b"head\nnew1\nnew2\ntail\n"

    def test_unique_legacy_deletion_anchors_moved_insertion(self):
        """A unique old side can prove a moved replacement insertion."""
        source = b"head\nnew\nneighbor\ntail\n"
        working = b"head\nold\nneighbor\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["2"],
            [
                AbsenceClaim(
                    anchor_line=1,
                    content_lines=[b"old\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"head\n",
                        before_line=3,
                        before_content=b"neighbor\n",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                2: BaselineReference(
                    after_line=3,
                    after_content=b"neighbor\n",
                    before_line=4,
                    before_content=b"tail\n",
                    has_before_line=True,
                )
            },
        )

        assert merge_batch(source, ownership, working) == (
            b"head\nneighbor\nnew\ntail\n"
        )

    def test_split_replacement_origin_refuses_missing_parent_boundary(self):
        """Split replacement units should fail rather than guess a new location."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold2\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"old1",
                        before_line=4,
                        before_content=b"tail",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                3: BaselineReference(
                    after_line=2,
                    after_content=b"old1",
                    before_line=4,
                    before_content=b"tail",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "replace target lines 2 with source lines 3",
        ]

        result = merge_batch(
            source,
            ownership,
            working,
            resolution=candidate_set.candidates[0].resolution,
        )

        assert result == b"head\nnew2\ntail\n"

    def test_split_replacement_origin_enumerates_ambiguous_placements(self):
        """Missing parent context should use reviewed candidates, not guessing."""
        source = b"head\nnew1\nnew2\ntail\n"
        working = b"head\nold2\nmid\nold2\ntail\n"
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old2\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"old1",
                        before_line=4,
                        before_content=b"tail",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                3: BaselineReference(
                    after_line=2,
                    after_content=b"old1",
                    before_line=4,
                    before_content=b"tail",
                    has_before_line=True,
                )
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        with pytest.raises(MergeError, match="original replacement boundary"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "replace target lines 2 with source lines 3",
            "replace target lines 4 with source lines 3",
        ]
        first, second = candidate_set.candidates
        assert merge_batch(
            source,
            ownership,
            working,
            resolution=first.resolution,
        ) == b"head\nnew2\nmid\nold2\ntail\n"
        assert merge_batch(
            source,
            ownership,
            working,
            resolution=second.resolution,
        ) == b"head\nold2\nmid\nnew2\ntail\n"

    def test_baseline_referenced_independent_presence_and_absence(self):
        """Independent baseline-coordinate insertions and removals can compose."""
        source = b"x\nsame\nsame\nc\nsame\nc\n"
        working = b"same\na\nc\n"
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            [
                AbsenceClaim(
                    anchor_line=5,
                    content_lines=[b"a\n"],
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"same",
                        before_line=3,
                        before_content=b"c",
                        has_before_line=True,
                    ),
                )
            ],
            baseline_references={
                1: BaselineReference(
                    after_line=None,
                    before_line=1,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
        )

        result = merge_batch(source, ownership, working)

        assert result == b"x\nsame\nc\n"

    def test_baseline_referenced_absence_declines_when_content_changed(self):
        """Baseline-coordinate fallback should not remove changed target bytes."""
        source = b"line1\nnew context\nline3\n"
        working = b"line1\nother value\nline3\n"
        ownership = BatchOwnership.from_presence_lines(
            [],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old value\n"],
                    baseline_reference=BaselineReference(after_line=1),
                )
            ],
        )

        with pytest.raises(MergeError):
            merge_batch(source, ownership, working)

    def test_merge_with_deletion_after_line(self):
        """Test deletion constraint removes content at specific position."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nunwanted\nline3\n"

        # Create absence claim to suppress "unwanted" after line 2
        deletions = [AbsenceClaim(anchor_line=2, content_lines=[b"unwanted\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the unwanted line
        assert result == b"line1\nline2\nline3\n"

    def test_merge_deletion_no_match_preserves_content(self):
        """Test that deletion constraint with no match preserves content."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\ndifferent\nline3\n"

        # Create absence claim for content that doesn't exist
        deletions = [AbsenceClaim(anchor_line=2, content_lines=[b"nonexistent\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should preserve all content (no match to suppress)
        assert result == b"line1\nline2\ndifferent\nline3\n"

    def test_merge_deletion_position_aware_not_global(self):
        """Test that deletion constraint is position-aware, not global removal.

        This validates that deletions suppress content at their anchored position,
        not globally throughout the file.
        """
        source = b"line1\nline2\nline3\n"
        working = b"duplicate\nline1\nduplicate\nline2\nline3\n"

        # Create absence claim anchored at start-of-file
        deletions = [AbsenceClaim(anchor_line=None, content_lines=[b"duplicate\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should only suppress first "duplicate" (at anchor position)
        # Second "duplicate" should remain (different structural position)
        lines = result.splitlines(keepends=True)
        duplicate_count = sum(1 for line in lines if line == b"duplicate\n")
        assert duplicate_count == 1, "Should only remove duplicate at anchored position"
        assert b"line1\n" in lines
        assert b"duplicate\n" in lines  # Second occurrence remains

    def test_merge_deletion_multiline_sequence(self):
        """Test deletion constraint with multi-line sequence."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nblock_start\nblock_end\nline2\nline3\n"

        # Create absence claim for multi-line sequence
        deletions = [AbsenceClaim(anchor_line=1, content_lines=[b"block_start\n", b"block_end\n"])]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Should remove the entire sequence
        assert result == b"line1\nline2\nline3\n"

    def test_merge_interleaved_even_odd_batches(self):
        """Test merging interleaved batches (pathological case from plan)."""
        # File with 10 lines
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"

        # Working tree with all lines removed
        working = b""

        # Batch 1: even lines (2, 4, 6, 8, 10)
        even_claimed = ["2", "4", "6", "8", "10"]

        # Apply even lines first
        result1 = merge_batch(source, BatchOwnership.from_presence_lines(even_claimed, []), working)
        assert result1 == b"line2\nline4\nline6\nline8\nline10\n"

        # Now apply odd lines on top of even
        odd_claimed = ["1", "3", "5", "7", "9"]
        result2 = merge_batch(source, BatchOwnership.from_presence_lines(odd_claimed, []), result1)

        # Should interleave correctly
        expected = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        assert result2 == expected

    def test_merge_interleaved_odd_then_even(self):
        """Test merging interleaved batches in reverse order."""
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        working = b""

        # Apply odd first
        odd_claimed = ["1", "3", "5", "7", "9"]
        result1 = merge_batch(source, BatchOwnership.from_presence_lines(odd_claimed, []), working)
        assert result1 == b"line1\nline3\nline5\nline7\nline9\n"

        # Then apply even
        even_claimed = ["2", "4", "6", "8", "10"]
        result2 = merge_batch(source, BatchOwnership.from_presence_lines(even_claimed, []), result1)

        # Should produce same result as even-then-odd
        expected = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"
        assert result2 == expected

    def test_merge_with_duplicate_lines_uses_alignment(self):
        """Test that merge uses structural alignment, not text matching."""
        # Source has duplicate "dup" lines
        source = b"line1\ndup\nline3\ndup\nline5\n"

        # Working tree is missing first dup
        working = b"line1\nline3\ndup\nline5\n"

        # Claim line 2 (first "dup")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first dup based on alignment, not text search
        # Result should have both dups in correct positions
        assert result == b"line1\ndup\nline3\ndup\nline5\n"

    def test_merge_with_low_entropy_duplicates_blank_lines(self):
        """Test alignment with duplicate blank lines (low-entropy content)."""
        # Source has multiple blank lines in specific positions
        source = b"line1\n\nline3\n\nline5\n"

        # Working tree missing first blank line
        working = b"line1\nline3\n\nline5\n"

        # Claim line 2 (first blank line)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first blank line at correct position via alignment
        assert result == b"line1\n\nline3\n\nline5\n"

    def test_merge_with_low_entropy_duplicates_braces(self):
        """Test alignment with duplicate braces (common in code)."""
        # Source has multiple closing braces
        source = b"func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

        # Working tree missing first closing brace
        working = b"func1() {\nfunc2() {\n}\nfunc3() {\n}\n"

        # Claim line 2 (first "}")
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Should insert first brace at correct position via alignment
        assert result == b"func1() {\n}\nfunc2() {\n}\nfunc3() {\n}\n"

    def test_merge_preserves_working_tree_reordering(self):
        """Test that working tree extras are preserved even when reordered."""
        source = b"A\nB\nC\n"
        working = b"A\nX\nB\nY\nC\nZ\n"

        # Claim all source lines (no-op for content, but tests preservation)
        claimed = ["1", "2", "3"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Extras should remain in their positions
        assert result == b"A\nX\nB\nY\nC\nZ\n"

    def test_merge_with_reordered_source_lines_in_working_tree(self):
        """Test merge when source lines are reordered in working tree."""
        # Batch source has A, B, C in order
        source = b"line1\nA\nB\nC\nline5\n"

        # Working tree has same lines but B and A swapped
        working = b"line1\nB\nA\nC\nline5\n"

        # Claim line 2 (A in batch source)
        claimed = ["2"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # A should already be present at line 3, so shouldn't duplicate
        # (semantic matching finds it despite different position)
        assert result.count(b"A\n") == 1
        assert b"A\n" in result

    def test_merge_claimed_range(self):
        """Test merge with claimed range."""
        source = b"line1\nline2\nline3\nline4\nline5\n"
        working = b"line1\nline5\n"

        # Claim lines 2-4
        claimed = ["2-4"]

        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        assert result == b"line1\nline2\nline3\nline4\nline5\n"

    def test_enumerates_contextual_presence_gaps(self):
        """Distinctive outer anchors expose ambiguous insertion gaps."""
        source = b"""header
old one
old two
old three
claimed
old tail
footer
"""
        working = b"""header
target one
target two
footer
"""
        ownership = BatchOwnership.from_presence_lines(["5"], [])

        with pytest.raises(MergeError, match="different version"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "insert source lines 5-5 after target line 1, before target line 2",
            "insert source lines 5-5 after target line 2, before target line 3",
            "insert source lines 5-5 after target line 3, before target line 4",
        ]
        assert [
            merge_batch(
                source,
                ownership,
                working,
                resolution=candidate.resolution,
            )
            for candidate in candidate_set.candidates
        ] == [
            b"header\nclaimed\ntarget one\ntarget two\nfooter\n",
            b"header\ntarget one\nclaimed\ntarget two\nfooter\n",
            b"header\ntarget one\ntarget two\nclaimed\nfooter\n",
        ]

    def test_repeated_presence_candidates_apply_reviewed_coordinates(self):
        """A candidate must override an ambiguous saved insertion coordinate."""
        source = b"""header
old one
old two
old three
claimed
old tail
footer
"""
        working = b"""header
same
same
same
footer
"""
        ownership = BatchOwnership.from_presence_lines(
            ["5"],
            [],
            baseline_references={
                5: BaselineReference(
                    after_line=2,
                    after_content=b"same",
                    before_line=3,
                    before_content=b"same",
                    has_before_line=True,
                )
            },
        )

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "insert source lines 5-5 after target line 1, before target line 2",
            "insert source lines 5-5 after target line 2, before target line 3",
            "insert source lines 5-5 after target line 3, before target line 4",
            "insert source lines 5-5 after target line 4, before target line 5",
        ]
        assert [
            merge_batch(
                source,
                ownership,
                working,
                resolution=candidate.resolution,
            )
            for candidate in candidate_set.candidates
        ] == [
            b"header\nclaimed\nsame\nsame\nsame\nfooter\n",
            b"header\nsame\nclaimed\nsame\nsame\nfooter\n",
            b"header\nsame\nsame\nclaimed\nsame\nfooter\n",
            b"header\nsame\nsame\nsame\nclaimed\nfooter\n",
        ]

    def test_contextual_presence_candidates_honor_preview_cap(self):
        """Wide ambiguous intervals should stop at the candidate safety cap."""
        source = b"header\nold one\nold two\nold three\nclaimed\nold tail\nfooter\n"
        working = b"header\none\ntwo\nthree\nfour\nfooter\n"
        ownership = BatchOwnership.from_presence_lines(["5"], [])

        with pytest.raises(MergeError, match="Too many merge candidates"):
            enumerate_merge_batch_candidates_from_line_sequences(
                source.splitlines(keepends=True),
                ownership,
                working.splitlines(keepends=True),
                max_candidates=3,
            )

    @pytest.mark.parametrize("max_candidates", [0, 51, True, 1.0, None])
    def test_candidate_enumeration_rejects_unsupported_caps(
        self,
        max_candidates,
    ):
        """Public candidate limits must fit the merge application safety cap."""
        ownership = BatchOwnership.from_presence_lines(["1"], [])

        with pytest.raises(
            ValueError,
            match="max_candidates must be between 1 and 50",
        ):
            enumerate_merge_batch_candidates_from_line_sequences(
                [b"claimed\n"],
                ownership,
                [],
                max_candidates=max_candidates,
            )

    def test_multiple_contextual_ambiguities_remain_a_hard_refusal(self):
        """Independent ambiguous runs should not form a candidate product."""
        source = b"""header
a old one
a old two
a old three
claimed a
a old tail
middle
b old one
b old two
b old three
claimed b
b old tail
footer
"""
        working = b"""header
a target one
a target two
middle
b target one
b target two
footer
"""
        ownership = BatchOwnership.from_presence_lines(["5", "11"], [])

        with pytest.raises(MergeError, match="different version"):
            merge_batch(source, ownership, working)

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert candidate_set.candidates == ()
        assert candidate_set.outcome is MergeCandidateSetOutcome.REFUSED

    def test_replacement_review_does_not_waive_insertion_ambiguity(self):
        """Reviewing one replacement cannot authorize an unrelated insertion."""
        source = b"head\nnew one\nnew two\nclaimed\ntail\n"
        working = b"head\nold two\nmid\nold two\nsame\nsame\nsame\ntail\n"
        replacement_reference = BaselineReference(
            after_line=2,
            after_content=b"old one",
            before_line=4,
            before_content=b"tail",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["3,4"],
            [
                AbsenceClaim(
                    anchor_line=2,
                    content_lines=[b"old two\n"],
                    baseline_reference=replacement_reference,
                )
            ],
            baseline_references={
                3: replacement_reference,
                4: BaselineReference(
                    after_line=5,
                    after_content=b"same",
                    before_line=6,
                    before_content=b"same",
                    has_before_line=True,
                ),
            },
            replacement_units=[
                ReplacementUnit(
                    presence_lines=["3"],
                    deletion_indices=[0],
                    origin=ReplacementUnitOrigin(
                        old_start=2,
                        old_end=3,
                        new_start=2,
                        new_end=3,
                        baseline_reference=BaselineReference(
                            after_line=1,
                            after_content=b"head",
                            before_line=4,
                            before_content=b"tail",
                            has_before_line=True,
                        ),
                    ),
                )
            ],
        )

        with pytest.raises(MergeError):
            enumerate_merge_batch_candidates_from_line_sequences(
                source.splitlines(keepends=True),
                ownership,
                working.splitlines(keepends=True),
                max_candidates=10,
            )

    def test_enumerates_displaced_absence_candidates(self):
        """Ambiguous nearby deletion content can be previewed as candidates."""
        source = [b"a\n", b"b\n"]
        working = [b"a\n", b"insert\n", b"x\n", b"x\n", b"b\n"]
        ownership = BatchOwnership(
            [],
            [AbsenceClaim(anchor_line=1, content_lines=[b"x\n"])],
        )

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source,
            ownership,
            working,
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "delete target line 3",
            "delete target line 4",
        ]

    def test_repeated_absence_candidates_apply_reviewed_coordinates(self):
        """A candidate must override an ambiguous saved deletion coordinate."""
        source = b"a\nb\n"
        working = b"a\nsame\nremove\nend\nmid\nsame\nremove\nend\nb\n"
        ownership = BatchOwnership(
            [],
            [
                AbsenceClaim(
                    anchor_line=1,
                    content_lines=[b"remove\n"],
                    baseline_reference=BaselineReference(
                        after_line=2,
                        after_content=b"same",
                        before_line=4,
                        before_content=b"end",
                        has_before_line=True,
                    ),
                )
            ],
        )

        candidate_set = enumerate_merge_batch_candidates_from_line_sequences(
            source.splitlines(keepends=True),
            ownership,
            working.splitlines(keepends=True),
            max_candidates=10,
        )

        assert [candidate.summary for candidate in candidate_set.candidates] == [
            "delete target line 3",
            "delete target line 7",
        ]
        first, second = candidate_set.candidates
        assert merge_batch(
            source,
            ownership,
            working,
            resolution=first.resolution,
        ) == b"a\nsame\nend\nmid\nsame\nremove\nend\nb\n"
        assert merge_batch(
            source,
            ownership,
            working,
            resolution=second.resolution,
        ) == b"a\nsame\nremove\nend\nmid\nsame\nend\nb\n"

    def test_merge_multiple_deletions(self):
        """Test merge with multiple deletion constraints at different positions."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted1\nline1\nline2\nunwanted2\nline3\n"

        deletions = [
            AbsenceClaim(anchor_line=None, content_lines=[b"unwanted1\n"]),
            AbsenceClaim(anchor_line=2, content_lines=[b"unwanted2\n"])
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Both deletion constraints should be enforced
        assert result == b"line1\nline2\nline3\n"

    def test_merge_multiple_deletions_same_content(self):
        """Test that multiple deletion constraints for same content suppress all occurrences."""
        source = b"line1\nline2\nline3\n"
        working = b"unwanted\nline1\nline2\nunwanted\nline3\n"

        # Two deletion constraints for same content (e.g., from incremental batching)
        deletions = [
            AbsenceClaim(anchor_line=None, content_lines=[b"unwanted\n"]),
            AbsenceClaim(anchor_line=2, content_lines=[b"unwanted\n"])
        ]

        result = merge_batch(source, BatchOwnership([], deletions), working)

        # Both occurrences should be suppressed
        assert result == b"line1\nline2\nline3\n"

    def test_merge_preserves_existing_crlf_line_endings(self):
        """Test that merge keeps the target file's line endings."""
        source = b"line1\nline2\nline3\n"  # Already normalized
        working = b"line1\r\nline2\r\nline3\r\n"  # Windows line endings

        result = merge_batch(source, BatchOwnership([], []), working)

        assert result == working

    def test_merge_large_file_performance(self):
        """Test merge performance with large files (10k+ lines)."""
        # Create large source file
        source_lines = [f"line{i}\n".encode() for i in range(1, 10001)]
        source = b"".join(source_lines)

        # Working tree with 1000 lines inserted at top
        extra_lines = [f"extra{i}\n".encode() for i in range(1, 1001)]
        working = b"".join(extra_lines + source_lines)

        # Claim every 100th line
        claimed = [str(i) for i in range(100, 10001, 100)]

        # Structural matching should complete quickly for shifted large files.
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

        # Verify result has both extras and source
        assert result.startswith(b"extra1\n")
        assert b"line1\n" in result
        assert b"line10000\n" in result


class TestMergeErrors:
    """Tests for merge error conditions."""

    def test_merge_error_claimed_line_out_of_range(self):
        """Test error when claimed line is out of range."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        claimed = ["100"]  # Out of range

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_error_deletion_anchor_out_of_range(self):
        """Test error when deletion anchor is out of range."""
        source = b"line1\nline2\nline3\n"
        working = b"line1\nline2\nline3\n"

        deletions = [AbsenceClaim(anchor_line=100, content_lines=[b"unwanted\n"])]

        with pytest.raises(MergeError, match="out of range"):
            merge_batch(source, BatchOwnership([], deletions), working)

    def test_merge_error_claimed_line_no_context(self):
        """Test error when claimed line has no surrounding context."""
        # Source with lines 1-10
        source = b"\n".join([f"line{i}".encode() for i in range(1, 11)]) + b"\n"

        # Working tree completely rewritten (no alignment possible)
        working = b"\n".join([f"different{i}".encode() for i in range(1, 11)]) + b"\n"

        # Claim line 5 (middle line with no aligned neighbors)
        claimed = ["5"]

        # Should fail because cannot reliably place line 5
        with pytest.raises(MergeError, match="Cannot reliably place"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_succeeds_with_minimal_context(self):
        """Test that merge succeeds when there's minimal but sufficient context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing line 3 but has neighbors
        working = b"line1\nline2\nline4\nline5\n"

        # Claim missing line 3
        claimed = ["3"]

        # Should succeed because lines 2 and 4 provide context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert result == b"line1\nline2\nline3\nline4\nline5\n"

    def test_merge_succeeds_with_only_trailing_context(self):
        """Test merge with missing line that only has trailing (after) context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree missing lines 1-2 but has line3 onwards
        working = b"different1\ndifferent2\nline3\nline4\nline5\n"

        # Claim line 2 - no leading context but has trailing (line3)
        claimed = ["2"]

        # Should succeed - line3 provides trailing context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line2" in result

    def test_merge_succeeds_with_only_leading_context(self):
        """Test merge with missing line that only has leading (before) context."""
        source = b"line1\nline2\nline3\nline4\nline5\n"

        # Working tree has line1-3 but then different content
        working = b"line1\nline2\nline3\ndifferent4\ndifferent5\n"

        # Claim line 4 - has leading context (line3) but no trailing
        claimed = ["4"]

        # Should succeed - line3 provides leading context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line4" in result

    def test_merge_requires_context_even_at_edges(self):
        """Test that edge lines require context too (no special case)."""
        source = b"line1\nline2\nline3\n"

        # Working tree has middle line only
        working = b"different1\nline2\ndifferent3\n"

        # Claim first line - has context (line2 is aligned)
        claimed = ["1"]

        # Should succeed - line2 provides context
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)
        assert b"line1" in result

    def test_merge_edge_lines_fail_without_neighbors(self):
        """Test that edge lines fail when completely isolated."""
        source = b"line1\nline2\nline3\n"

        # Working tree completely different
        working = b"different1\ndifferent2\ndifferent3\n"

        # Claim first line with no aligned neighbors
        claimed = ["1"]

        # Should fail - file completely rewritten
        with pytest.raises(MergeError, match="file completely rewritten"):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, []), working)

    def test_merge_error_batch_created_from_later_file_state(self):
        """Test error when batch was created from later file state with extra context.

        This simulates the problem encountered during pristine history reconstruction:
        - A batch is created from final file state (many features added)
        - Batch is applied to earlier file state (features don't exist yet)
        - The batch contains changes that depend on context not yet in the file
        - Should raise MergeError rather than silently corrupting the file

        Scenario:
        - Later file state has parser_status section followed by parser_include section
        - Earlier file state only has parser_status section
        - Batch adds --porcelain argument to parser_status
        - Batch also has a deletion that removes the old set_defaults line
        - The deletion is adjacent to context that doesn't exist in earlier state
        """
        # Earlier file state: basic status command (6 lines)
        working_early = b"line1\nline2\nline3\nline4\nline5\nline6\n"

        # Later file state: status command + extra features (12 lines)
        # Lines 1-6: same as before
        # Lines 7-12: new parser_include section added AFTER status in later history
        source_later = b"line1\nline2\nline3\nmodified4\nline5\nline6\nline7\nline8\nline9\nline10\nline11\nline12\n"

        # Batch claims line 4 (modification to status section)
        # In source_later, line 4 has "modified4" instead of "line4"
        # The batch wants to merge this change back
        claimed = ["4"]  # Modified line in the middle of status section

        # But the batch also includes a DELETION that depends on context from lines 7-12
        # Specifically, it wants to delete line 6 with anchor near line 7
        deletions = [AbsenceClaim(anchor_line=7, content_lines=[b"line6\n"])]

        # Trying to merge should detect that:
        # 1. The deletion anchor (line 7 in source_later) doesn't exist in working_early
        # 2. Working tree only has 6 lines, source has 12
        # 3. The structural mismatch is too large to safely merge

        # This correctly raises MergeError because deletion anchor doesn't exist
        with pytest.raises(MergeError, match="anchor not present"):
            merge_batch(source_later, BatchOwnership.from_presence_lines(claimed, deletions), working_early)

    def test_merge_produces_corruption_with_mismatched_context(self):
        """Reproduce corruption when merging batch from later state to earlier state.

        Real scenario from pristine history reconstruction attempt:

        Working tree (earlier state - commit e05ce02c):
            Line 1: parser_status = subparsers.add_parser(...)
            Line 2:     "status",
            Line 3: )
            Line 4: parser_status.set_defaults(func=lambda _: ...)
            Line 5:
            Line 6: # Parse arguments

        Batch source (later state - commit a6af5fa6):
            Line 1: parser_status = subparsers.add_parser(...)
            Line 2:     "status",
            Line 3: )
            Line 4: parser_status.set_defaults(func=lambda _: ...)  [TO DELETE]
            Line 5: parser_status.add_argument("--porcelain"...)    [TO ADD]
            Line 6: parser_status.set_defaults(func=lambda args: ...)  [TO ADD]
            Line 7:
            Line 8: # include - Stage the selected hunk
            Line 9: parser_include = subparsers.add_parser(...)

        Batch ownership:
            - Claimed: line 5-6 (new --porcelain argument + new set_defaults)
            - Deletion: line 4 (old set_defaults) anchored after line 3

        When applied to working tree WITHOUT matching context (no parser_include):
            Result should be: lines 1-3, then new 5-6, then line 6
            Corruption: BOTH old line 4 AND new line 6 present (duplicate set_defaults)
        """
        # Working tree: only 6 lines, ends after old set_defaults
        working = b"""parser_status = subparsers.add_parser(
    "status",
)
parser_status.set_defaults(func=lambda _: commands.command_status())

# Parse arguments
"""

        # Batch source: 9+ lines, has parser_include section after
        source = b"""parser_status = subparsers.add_parser(
    "status",
)
parser_status.set_defaults(func=lambda _: commands.command_status())
parser_status.add_argument("--porcelain", action="store_true")
parser_status.set_defaults(func=lambda args: commands.command_status(porcelain=args.porcelain))

# include - Stage the selected hunk
parser_include = subparsers.add_parser(
"""

        # Batch wants to:
        # 1. Delete line 4 (old set_defaults) with anchor after line 3
        # 2. Add lines 5-6 (--porcelain arg + new set_defaults)
        deletions = [AbsenceClaim(
            anchor_line=3,
            content_lines=[b"parser_status.set_defaults(func=lambda _: commands.command_status())\n"]
        )]
        claimed = ["5", "6"]  # New argument and new set_defaults

        # Apply the merge
        result = merge_batch(source, BatchOwnership.from_presence_lines(claimed, deletions), working)

        # Check that old and new set_defaults are not both present.
        result_str = result.decode()
        old_setdefaults = "lambda _: commands.command_status()"
        new_setdefaults = "lambda args: commands.command_status(porcelain=args.porcelain)"

        has_old = old_setdefaults in result_str
        has_new = new_setdefaults in result_str

        print("\n=== MERGE RESULT ===")
        print(result_str)
        print("=== END RESULT ===")
        print(f"Has old set_defaults: {has_old}")
        print(f"Has new set_defaults: {has_new}")

        if has_old and has_new:
            pytest.fail(
                "both old and new set_defaults present. "
                "The deletion didn't work correctly when context doesn't match."
            )

    def test_batch_with_changes_to_nonexistent_sections(self):
        """Test applying batch that modifies sections not present in working tree.

        Real corruption scenario from classification:

        Batch contains (from working tree diff):
            Section A changes (parser_show): Remove --line, --file args
            Section B changes (parser_status): Add --porcelain arg  <-- intended
            Section C changes (parser_include): Simplify --file arg

        Working tree (earlier commit e05ce02c):
            Only has basic Section B (parser_status)
            Does not have Sections A or C yet.

        When batch applied:
            - Section A deletions fail to match (parser_show doesn't exist)
            - Section B changes apply (parser_status exists)
            - Section C deletions fail to match (parser_include doesn't exist)
            - Result: partial application with context confusion

        The batch was created correctly for working tree state,
        but contains changes across multiple code sections.
        When those sections don't all exist in target, merge fails.
        """
        # Working tree: only section B exists
        working = b"""# Section B
line1_b
line2_b
line3_b
"""

        # Batch source: all sections exist
        source = b"""# Section A
line1_a
line2_a
line3_a

# Section B
line1_b
line2_b_MODIFIED
line3_b

# Section C
line1_c
line2_c
line3_c
"""

        # Batch modifies line in Section B (which exists in working)
        # But also has deletions from Section A (which doesn't exist)
        claimed = ["7"]  # line2_b_MODIFIED in source
        deletions = [
            # Try to delete from Section A (anchor at line 2)
            AbsenceClaim(anchor_line=2, content_lines=[b"line1_a\n"])
        ]

        # This should fail because Section A doesn't exist in working tree
        # The anchor line 2 doesn't map correctly
        with pytest.raises(MergeError):
            merge_batch(source, BatchOwnership.from_presence_lines(claimed, deletions), working)


class TestDiscardBatch:
    """Tests for discard_batch function (inverse of merge_batch)."""

    def test_discard_simple_claimed_line(self):
        """Test discarding a single claimed line restores baseline."""
        baseline = b"original\n"
        batch_source = b"modified\n"
        working = b"modified\n"

        # Claim the modified line
        ownership = BatchOwnership.from_presence_lines(["1"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore baseline
        assert result == b"original\n"

    def test_discard_preserves_non_batch_content(self):
        """Test that non-batch content is preserved."""
        baseline = b"line1\nline2\nline3\n"
        batch_source = b"line1\nmodified2\nline3\n"
        working = b"line1\nmodified2\nextra\nline3\n"

        # Claim only line 2 (modified2)
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore line2 from baseline, keep extra line
        assert result == b"line1\nline2\nextra\nline3\n"

    def test_discard_uses_replacement_edges_around_copied_baseline_content(self):
        """Recorded replacement edges prevent copied content stealing alignment."""
        baseline = (
            b"old-header\n{\n common\n old-only\n}\nold-sep\ntail\n"
        )
        batch_source = (
            b"new-header\n{\n common\n new-only\n}\nold-sep\n"
            b"old-header\n{\n common\n}\ntail\n"
        )
        working = batch_source + b"LOCAL\n"
        copied_reference = BaselineReference(
            after_line=6,
            after_content=b"old-sep\n",
            before_line=7,
            before_content=b"tail\n",
            has_before_line=True,
        )
        deletions = [
            AbsenceClaim(
                anchor_line=None,
                content_lines=[b"old-header\n"],
                baseline_reference=BaselineReference(
                    after_line=None,
                    before_line=2,
                    before_content=b"{\n",
                    has_before_line=True,
                ),
            ),
            AbsenceClaim(
                anchor_line=3,
                content_lines=[b" old-only\n"],
                baseline_reference=BaselineReference(
                    after_line=3,
                    after_content=b" common\n",
                    before_line=5,
                    before_content=b"}\n",
                    has_before_line=True,
                ),
            ),
        ]
        ownership = BatchOwnership.from_presence_lines(
            ["1", "4", "7-10"],
            deletions,
            baseline_references={
                line: copied_reference for line in range(7, 11)
            },
            replacement_units=[
                ReplacementUnit(["1"], [0]),
                ReplacementUnit(["4"], [1]),
            ],
        )

        result = discard_batch(batch_source, ownership, working, baseline)

        assert result == baseline + b"LOCAL\n"

    def test_discard_removes_copied_bof_insertion_idempotently(self):
        """Referenced copied content is removed once without touching local edits."""
        baseline = b"A\nB\n"
        batch_source = b"A\nB\nA\nB\n"
        working = batch_source + b"LOCAL\n"
        reference = BaselineReference(
            after_line=None,
            before_line=1,
            before_content=b"A\n",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["1-2"],
            baseline_references={1: reference, 2: reference},
        )

        result = discard_batch(batch_source, ownership, working, baseline)

        assert result == baseline + b"LOCAL\n"
        assert (
            discard_batch(batch_source, ownership, result, baseline)
            == result
        )

    def test_discard_repeated_bof_copy_is_idempotent(self):
        """Repeated boundary lines do not make a second discard destructive."""
        baseline = b"A\nA\n"
        batch_source = b"A\nA\nA\n"
        reference = BaselineReference(
            after_line=None,
            before_line=1,
            before_content=b"A\n",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["1"],
            baseline_references={1: reference},
        )

        result = discard_batch(
            batch_source,
            ownership,
            batch_source,
            baseline,
        )

        assert result == baseline
        assert (
            discard_batch(batch_source, ownership, result, baseline)
            == baseline
        )

    @pytest.mark.parametrize(
        "first_gap",
        [b"Q\n", b""],
        ids=["intended-gap-diverged", "intended-gap-already-absent"],
    )
    def test_discard_refuses_insertion_from_ambiguous_source_clone(
        self,
        first_gap,
    ):
        """Discard never removes an insertion from an unrelated source clone."""
        baseline = b"H\nA\nB\nT\n"
        batch_source = b"H\nA\nX\nB\nT\n"
        working = (
            b"P\nH\nA\n"
            + first_gap
            + b"B\nT\nU\nH\nA\nX\nB\nT\n"
        )
        reference = BaselineReference(
            after_line=2,
            after_content=b"A\n",
            before_line=3,
            before_content=b"B\n",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            ["3"],
            baseline_references={3: reference},
        )

        with pytest.raises(MergeError, match="different version"):
            discard_batch(batch_source, ownership, working, baseline)

    def test_discard_after_divergence(self):
        """Test discarding after working tree diverged from batch source."""
        baseline = b"A\nB\nC\nD\nE\n"
        batch_source = b"A\nB_modified\nC\nD\nE\n"
        # Working tree added lines at top
        working = b"X\nY\nZ\nA\nB_modified\nC\nD\nE\n"

        # Claim the modified B
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline, keep X, Y, Z
        assert result == b"X\nY\nZ\nA\nB\nC\nD\nE\n"

    def test_discard_with_insertion(self):
        """Test discarding insertion removes it."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninserted\nline2\n"  # Batch added "inserted"
        working = b"line1\ninserted\nline2\n"

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["2"], [])  # Line 2 of batch_source is "inserted"

        result = discard_batch(batch_source, ownership, working, baseline)

        # Insertion should be removed (maps to "insert" region with no baseline)
        assert result == b"line1\nline2\n"

    def test_discard_with_insertion_at_start(self):
        """Test discarding insertion at start of file."""
        baseline = b"line1\nline2\n"
        batch_source = b"inserted\nline1\nline2\n"  # Batch added "inserted" at start
        working = b"inserted\nline1\nline2\n"

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["1"], [])  # Line 1 of batch_source is "inserted"

        result = discard_batch(batch_source, ownership, working, baseline)

        # Start insertion should be removed
        assert result == b"line1\nline2\n"

    def test_discard_combined_claimed_and_insertion(self):
        """Test discarding both claimed lines and insertions."""
        baseline = b"A\nB\nC\n"
        batch_source = b"A\nB_modified\ninserted\nC\n"  # Modified B and added "inserted"
        working = b"A\nB_modified\ninserted\nC\n"

        # Claim both modified B and the insertion
        ownership = BatchOwnership.from_presence_lines(["2", "3"], [])  # Lines 2 and 3 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore B from baseline (replace region) and remove insertion (insert region)
        assert result == b"A\nB\nC\n"

    def test_discard_multiple_insertions_same_position(self):
        """Test discarding multiple insertions at same position."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninsert1\ninsert2\nline2\n"  # Batch added two lines
        working = b"line1\ninsert1\ninsert2\nline2\n"

        # Claim both inserted lines
        ownership = BatchOwnership.from_presence_lines(["2", "3"], [])  # Lines 2 and 3 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Both insertions removed (both are insert regions with no baseline)
        assert result == b"line1\nline2\n"

    def test_discard_interleaved_batch_restores_baseline(self):
        """Test discarding interleaved batch (related to even/odd pathological case)."""
        baseline = b"1\n2\n3\n4\n5\n"
        batch_source = b"1\n2_mod\n3\n4_mod\n5\n"
        working = b"1\n2_mod\n3\n4_mod\n5\n"

        # Claim even lines (2, 4)
        ownership = BatchOwnership.from_presence_lines(["2", "4"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Should restore even lines from baseline
        assert result == b"1\n2\n3\n4\n5\n"

    def test_discard_rejects_partial_by_hunk_ownership(self):
        """Partial ownership of a by-hunk replace region is unsafe."""
        baseline = b"A\nold1\nold2\nD\n"
        batch_source = b"A\nnew1\nnew2\nnew3\nD\n"
        working = b"A\nnew1\nnew2\nnew3\nD\n"
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        with pytest.raises(MergeError, match="batch owns 1 of 3 lines"):
            discard_batch(batch_source, ownership, working, baseline)

    def test_discard_rejects_partial_ambiguous_repeated_replacement(self):
        """Repeated baseline/source content must not be guessed partially."""
        baseline = b"A\nA\nA\n"
        batch_source = b"B\nA\nB\n"
        working = b"B\nA\nB\n"
        ownership = BatchOwnership.from_presence_lines(["1"], [])

        with pytest.raises(MergeError, match="batch owns 1 of 3 lines"):
            discard_batch(batch_source, ownership, working, baseline)

    def test_discard_restores_full_ambiguous_repeated_replacement(self):
        """Full ownership can restore an ambiguous repeated replacement."""
        baseline = b"A\nA\nA\n"
        batch_source = b"B\nA\nB\n"
        working = b"B\nA\nB\n"
        ownership = BatchOwnership.from_presence_lines(["1-3"], [])

        assert discard_batch(batch_source, ownership, working, baseline) == baseline

    def test_discard_insertion_not_present_does_nothing(self):
        """Test that discarding insertion when not in working tree does nothing."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\ninserted\nline2\n"  # Batch added "inserted"
        working = b"line1\nline2\n"  # But working tree doesn't have it

        # Claim the inserted line
        ownership = BatchOwnership.from_presence_lines(["2"], [])  # Line 2 of batch_source

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged (insertion not present, nothing to discard)
        assert result == b"line1\nline2\n"

    def test_discard_claimed_line_not_present_does_nothing(self):
        """Test that discarding missing claimed line doesn't affect working tree."""
        baseline = b"line1\nline2\n"
        batch_source = b"line1\nmodified2\n"
        working = b"line1\nline2\n"  # Already at baseline

        # Claim line 2, but it's not present in working tree
        ownership = BatchOwnership.from_presence_lines(["2"], [])

        result = discard_batch(batch_source, ownership, working, baseline)

        # Working tree unchanged
        assert result == b"line1\nline2\n"
