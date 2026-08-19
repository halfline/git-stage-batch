"""Tests for presence-aware structural line mapping."""

import gc
from itertools import product
import tracemalloc

import pytest

import git_stage_batch.batch.merge.presence_mapping as presence_mapping_module
from git_stage_batch.batch.line_matching.line_mapping import (
    LineMapping,
    allocate_line_mapping,
)
from git_stage_batch.batch.line_matching.match import match_lines
from git_stage_batch.batch.line_matching.match_workspace import MatcherWorkspace
from git_stage_batch.batch.merge.presence_mapping import (
    match_lines_preserving_unowned_context,
)
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.line_selection import LineRanges


_HEAP_GROWTH_TOLERANCE = 256 * 1024


def test_repeated_input_mapping_preserves_structural_invariants() -> None:
    """Exhaustive small repeats must stay monotonic, bijective, and content-equal."""
    payloads = (b"A\n", b"B\n")
    for source_count in range(1, 5):
        for target_count in range(5):
            for source in product(payloads, repeat=source_count):
                for target in product(payloads, repeat=target_count):
                    for selected_mask in range(1, 1 << source_count):
                        controlled = LineRanges.from_lines(
                            source_index + 1
                            for source_index in range(source_count)
                            if selected_mask & (1 << source_index)
                        )
                        result = match_lines_preserving_unowned_context(
                            source,
                            target,
                            controlled,
                        )
                        try:
                            pairs = list(result.mapping.mapped_line_pairs())
                            assert all(
                                source_line < next_source_line
                                and target_line < next_target_line
                                for (source_line, target_line), (
                                    next_source_line,
                                    next_target_line,
                                ) in zip(pairs, pairs[1:])
                            )
                            assert all(
                                source[source_line - 1] == target[target_line - 1]
                                for source_line, target_line in pairs
                            )
                            assert len(
                                {source_line for source_line, _ in pairs}
                            ) == len(pairs)
                            assert len(
                                {target_line for _, target_line in pairs}
                            ) == len(pairs)
                        finally:
                            if result.owned:
                                result.mapping.close()


def test_distinctive_unowned_run_displaces_claimed_duplicate() -> None:
    """A selected duplicate cannot consume a distinctive live context run."""
    source = [
        b"head\n",
        b"shared\n",
        b"new tail\n",
        b"shared\n",
        b"prior tail\n",
        b"tail\n",
    ]
    target = [b"head\n", b"shared\n", b"prior tail\n", b"tail\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(2, 3)]),
    )
    try:
        assert result.corrected
        assert not result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (4, 2),
            (5, 3),
            (6, 4),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_non_enclosing_references_do_not_suppress_distinctive_context() -> None:
    """Scattered reconstructed-file claims are not insertion boundaries."""
    source = [
        b"head\n",
        b"shared\n",
        b"new tail\n",
        b"shared\n",
        b"prior tail\n",
        b"tail\n",
    ]
    target = [b"head\n", b"shared\n", b"prior tail\n", b"tail\n"]
    presence_lines = LineRanges.from_ranges([(2, 3)])
    reconstructed_file_reference = BaselineReference(
        after_line=None,
        before_line=None,
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2-3"],
        baseline_references={
            2: reconstructed_file_reference,
            3: reconstructed_file_reference,
        },
    )

    result = match_lines_preserving_unowned_context(
        source,
        target,
        presence_lines,
        ownership=ownership,
        presence_lines=presence_lines,
    )
    try:
        assert result.corrected
        assert not result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (4, 2),
            (5, 3),
            (6, 4),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_repeated_unanchored_context_does_not_displace_claimed_line() -> None:
    """A context-only pass cannot turn a repeated skeleton into authority."""
    source = [
        b"head\n",
        b"shared\n",
        b"shared\n",
        b"claimed tail\n",
        b"tail\n",
    ]
    target = [b"head\n", b"shared\n", b"tail\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(2, 2), (4, 4)]),
    )
    try:
        assert not result.corrected
        assert result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (2, 2),
            (5, 3),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_unmapped_repeated_context_remains_ambiguous() -> None:
    """A conservative context pass cannot silently bless a selected duplicate."""
    source = [b"shared\n", b"shared\n", b"shared\n"]
    target = [b"shared\n", b"unrelated\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(1, 1)]),
    )
    try:
        assert not result.corrected
        assert result.ambiguous
        assert not result.competing_context
        assert list(result.mapping.mapped_line_pairs()) == [(1, 1)]
    finally:
        if result.owned:
            result.mapping.close()


def test_two_distinctive_spans_competing_for_one_line_are_ambiguous() -> None:
    """Unique context on both sides cannot decide which shared line survived."""
    source = [
        b"unique selected anchor\n",
        b"shared\n",
        b"shared\n",
        b"tail\n",
    ]
    target = [b"unique selected anchor\n", b"shared\n", b"tail\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(1, 2)]),
    )
    try:
        assert not result.corrected
        assert result.ambiguous
        assert result.competing_context
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (2, 2),
            (4, 3),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_unique_selected_span_beats_unanchored_repeated_context() -> None:
    """A selected exact span survives a context-only repeated-line guess."""
    source = [
        b"unique selected anchor\n",
        b"shared\n",
        b"shared\n",
        b"shared\n",
    ]
    target = [b"unique selected anchor\n", b"shared\n", b"unrelated\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(1, 2)]),
    )
    try:
        assert not result.corrected
        assert not result.ambiguous
        assert not result.competing_context
        assert list(result.mapping.mapped_line_pairs()) == [(1, 1), (2, 2)]
    finally:
        if result.owned:
            result.mapping.close()


def test_explicit_alternative_authorizes_repeated_context() -> None:
    """Verified adjacent old-side metadata may anchor repeated context."""
    source = [
        b"head\n",
        b"shared\n",
        b"shared\n",
        b"claimed tail\n",
        b"tail\n",
    ]
    target = [b"head\n", b"shared\n", b"tail\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(2, 2), (4, 4)]),
        preferred_context_lines=LineRanges.from_ranges([(3, 3)]),
    )
    try:
        assert result.corrected
        assert not result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (3, 2),
            (5, 3),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_explicit_alternative_does_not_authorize_repeated_neighbor() -> None:
    """An exact old-side line cannot bless adjacent low-entropy context."""
    source = [b"A\n", b"B\n", b"A\n", b"B\n"]
    target = [b"A\n", b"B\n"]

    result = match_lines_preserving_unowned_context(
        source,
        target,
        LineRanges.from_ranges([(1, 2)]),
        preferred_context_lines=LineRanges.from_ranges([(3, 3)]),
    )
    try:
        assert result.corrected
        assert result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [(3, 1)]
    finally:
        if result.owned:
            result.mapping.close()


def test_partial_explicit_alternative_does_not_displace_applied_span() -> None:
    """One repeated old-side line cannot undo an exact applied replacement."""
    source = [
        b"head\n",
        b"selected anchor\n",
        b"shared\n",
        b"shared\n",
        b"old-only\n",
        b"tail\n",
    ]
    target = [b"head\n", b"selected anchor\n", b"shared\n", b"tail\n"]

    def context_matcher(*_args, **_kwargs):
        return LineMapping(
            source_to_target=[1, 0, 0, 3, 0, 4],
            target_to_source=[1, 0, 4, 6],
        )

    with LineMapping(
        source_to_target=[1, 2, 3, 0, 0, 4],
        target_to_source=[1, 2, 3, 6],
    ) as ordinary_mapping:
        result = match_lines_preserving_unowned_context(
            source,
            target,
            LineRanges.from_ranges([(2, 3)]),
            preferred_context_lines=LineRanges.from_ranges([(4, 5)]),
            ordinary_mapping=ordinary_mapping,
            matcher=context_matcher,
        )
        assert not result.corrected
        assert not result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (2, 2),
            (3, 3),
            (6, 4),
        ]
        assert not result.owned


def test_unique_recorded_boundary_authorizes_repeated_context() -> None:
    """A complete saved insertion run can displace its stolen blank lines."""
    source = [
        b"head\n",
        b"\n",
        b"\n",
        b"owned\n",
        b"\n",
        b"\n",
        b"tail\n",
    ]
    target = [b"head\n", b"\n", b"\n", b"tail\n"]
    presence_lines = LineRanges.from_ranges([(4, 6)])
    reference = BaselineReference(
        after_line=1,
        after_content=b"\n",
        before_line=2,
        before_content=b"tail\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["4-6"],
        baseline_references={source_line: reference for source_line in range(4, 7)},
    )

    def context_matcher(*_args, **_kwargs):
        return LineMapping(
            source_to_target=[0, 2, 3, 0, 0, 0, 4],
            target_to_source=[0, 2, 3, 7],
        )

    with LineMapping(
        source_to_target=[1, 0, 0, 0, 2, 3, 4],
        target_to_source=[1, 5, 6, 7],
    ) as ordinary_mapping:
        result = match_lines_preserving_unowned_context(
            source,
            target,
            presence_lines,
            ownership=ownership,
            presence_lines=presence_lines,
            ordinary_mapping=ordinary_mapping,
            matcher=context_matcher,
        )
    try:
        assert result.corrected
        assert not result.ambiguous
        assert list(result.mapping.mapped_line_pairs()) == [
            (1, 1),
            (2, 2),
            (3, 3),
            (7, 4),
        ]
    finally:
        if result.owned:
            result.mapping.close()


def test_recorded_boundary_requires_the_complete_presence_run() -> None:
    """One referenced line cannot authorize neighboring selected duplicates."""
    source = [
        b"head\n",
        b"\n",
        b"\n",
        b"owned\n",
        b"\n",
        b"\n",
        b"tail\n",
    ]
    target = [b"head\n", b"\n", b"\n", b"tail\n"]
    presence_lines = LineRanges.from_ranges([(4, 6)])
    ownership = BatchOwnership.from_presence_lines(
        ["4-6"],
        baseline_references={
            4: BaselineReference(
                after_line=1,
                after_content=b"\n",
                before_line=2,
                before_content=b"tail\n",
                has_before_line=True,
            )
        },
    )

    def context_matcher(*_args, **_kwargs):
        return LineMapping(
            source_to_target=[0, 2, 3, 0, 0, 0, 4],
            target_to_source=[0, 2, 3, 7],
        )

    with LineMapping(
        source_to_target=[1, 0, 0, 0, 2, 3, 4],
        target_to_source=[1, 5, 6, 7],
    ) as ordinary_mapping:
        result = match_lines_preserving_unowned_context(
            source,
            target,
            presence_lines,
            ownership=ownership,
            presence_lines=presence_lines,
            ordinary_mapping=ordinary_mapping,
            matcher=context_matcher,
        )
    try:
        assert not result.corrected
        assert result.ambiguous
    finally:
        if result.owned:
            result.mapping.close()


def test_repeated_recorded_boundary_remains_ambiguous() -> None:
    """Saved coordinates cannot choose between repeated live boundaries."""
    source = [
        b"head\n",
        b"\n",
        b"\n",
        b"owned\n",
        b"\n",
        b"\n",
        b"tail\n",
    ]
    target = [b"head\n", b"\n", b"\n", b"tail\n", b"\n", b"tail\n"]
    presence_lines = LineRanges.from_ranges([(4, 6)])
    reference = BaselineReference(
        after_line=1,
        after_content=b"\n",
        before_line=2,
        before_content=b"tail\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["4-6"],
        baseline_references={source_line: reference for source_line in range(4, 7)},
    )

    def context_matcher(*_args, **_kwargs):
        return LineMapping(
            source_to_target=[0, 2, 3, 0, 0, 0, 4],
            target_to_source=[0, 2, 3, 7, 0, 0],
        )

    with LineMapping(
        source_to_target=[1, 0, 0, 0, 2, 3, 4],
        target_to_source=[1, 5, 6, 7, 0, 0],
    ) as ordinary_mapping:
        result = match_lines_preserving_unowned_context(
            source,
            target,
            presence_lines,
            ownership=ownership,
            presence_lines=presence_lines,
            ordinary_mapping=ordinary_mapping,
            matcher=context_matcher,
        )
    try:
        assert not result.corrected
        assert result.ambiguous
    finally:
        if result.owned:
            result.mapping.close()


def test_distinctive_context_cannot_choose_a_repeated_recorded_boundary() -> None:
    """Unique nearby context is not authority for referenced placement."""
    source = [
        b"head\n",
        b"unique context\n",
        b"boundary a\n",
        b"owned\n",
        b"unique context\n",
        b"boundary a\n",
        b"boundary b\n",
        b"tail\n",
    ]
    target = [
        b"head\n",
        b"unique context\n",
        b"boundary a\n",
        b"boundary b\n",
        b"boundary a\n",
        b"boundary b\n",
        b"tail\n",
    ]
    presence_lines = LineRanges.from_ranges([(4, 6)])
    reference = BaselineReference(
        after_line=1,
        after_content=b"boundary a\n",
        before_line=2,
        before_content=b"boundary b\n",
        has_before_line=True,
    )
    ownership = BatchOwnership.from_presence_lines(
        ["4-6"],
        baseline_references={4: reference, 5: reference, 6: reference},
    )

    def context_matcher(*_args, **_kwargs):
        return LineMapping(
            source_to_target=[1, 2, 3, 0, 0, 0, 4, 7],
            target_to_source=[1, 2, 3, 7, 0, 0, 8],
        )

    with LineMapping(
        source_to_target=[1, 0, 0, 0, 2, 3, 4, 7],
        target_to_source=[1, 5, 6, 7, 0, 0, 8],
    ) as ordinary_mapping:
        result = match_lines_preserving_unowned_context(
            source,
            target,
            presence_lines,
            ownership=ownership,
            presence_lines=presence_lines,
            ordinary_mapping=ordinary_mapping,
            matcher=context_matcher,
        )
    try:
        assert not result.corrected
        assert result.ambiguous
    finally:
        if result.owned:
            result.mapping.close()


def test_no_collision_uses_only_the_ordinary_matcher() -> None:
    """Distinct selected payloads keep the one-pass mapping fast path."""
    calls = 0

    def counting_matcher(*args, **kwargs):
        nonlocal calls
        calls += 1
        return match_lines(*args, **kwargs)

    result = match_lines_preserving_unowned_context(
        [b"head\n", b"selected\n", b"tail\n"],
        [b"head\n", b"tail\n"],
        LineRanges.from_ranges([(2, 2)]),
        matcher=counting_matcher,
    )
    try:
        assert calls == 1
        assert not result.corrected
        assert not result.ambiguous
    finally:
        if result.owned:
            result.mapping.close()


def test_mapping_cleanup_catches_cancellation(monkeypatch) -> None:
    """Both owned mappings close if context authorization is cancelled."""
    mappings = []

    def tracking_matcher(*args, **kwargs):
        mapping = match_lines(*args, **kwargs)
        mappings.append(mapping)
        return mapping

    def cancel_authorization(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        presence_mapping_module,
        "_authorized_context_corrections",
        cancel_authorization,
    )

    with pytest.raises(KeyboardInterrupt):
        match_lines_preserving_unowned_context(
            [
                b"head\n",
                b"shared\n",
                b"new tail\n",
                b"shared\n",
                b"prior tail\n",
                b"tail\n",
            ],
            [b"head\n", b"shared\n", b"prior tail\n", b"tail\n"],
            LineRanges.from_ranges([(2, 3)]),
            matcher=tracking_matcher,
        )

    assert len(mappings) == 2
    for mapping in mappings:
        with pytest.raises(ValueError, match="mapping is closed"):
            list(mapping.mapped_line_pairs())


def test_workspace_cleanup_failure_closes_unreturned_mapping(monkeypatch) -> None:
    """A mapping transferred before context exit must close if exit fails."""
    mappings = []

    def tracking_matcher(*args, **kwargs):
        mapping = match_lines(*args, **kwargs)
        mappings.append(mapping)
        return mapping

    class CancellingWorkspace(MatcherWorkspace):
        def close(self):
            super().close()
            raise KeyboardInterrupt("workspace close cancelled")

    monkeypatch.setattr(
        presence_mapping_module,
        "MatcherWorkspace",
        CancellingWorkspace,
    )

    with pytest.raises(KeyboardInterrupt, match="workspace close cancelled"):
        match_lines_preserving_unowned_context(
            [b"head\n", b"selected\n", b"tail\n"],
            [b"head\n", b"tail\n"],
            LineRanges.from_ranges([(2, 2)]),
            matcher=tracking_matcher,
        )

    assert len(mappings) == 1
    with pytest.raises(ValueError, match="mapping is closed"):
        list(mappings[0].mapped_line_pairs())


def test_successful_mapping_survives_an_outer_exception_handler() -> None:
    """Caller exception state must not be mistaken for a local mapping failure."""
    try:
        raise RuntimeError("outer failure")
    except RuntimeError:
        result = match_lines_preserving_unowned_context(
            [b"head\n", b"selected\n", b"tail\n"],
            [b"head\n", b"tail\n"],
            LineRanges.from_ranges([(2, 2)]),
        )

    try:
        assert list(result.mapping.mapped_line_pairs()) == [(1, 1), (3, 2)]
    finally:
        if result.owned:
            result.mapping.close()


def test_context_correction_avoids_line_scale_python_heap() -> None:
    """File-sized occurrence and mapping state stays in mapped storage."""
    heap_peaks = []
    for filler_count in (512, 8192):
        prefix = b"".join(
            f"filler {line_index}\n".encode()
            for line_index in range(filler_count)
        )
        source_content = (
            prefix + b"shared\nnew tail\nshared\nprior tail\ntail\n"
        )
        target_content = prefix + b"shared\nprior tail\ntail\n"

        with (
            LineBuffer.from_bytes(source_content) as source_buffer,
            LineBuffer.from_bytes(target_content) as target_buffer,
            source_buffer.acquire_lines() as source_lines,
            target_buffer.acquire_lines() as target_lines,
        ):
            gc.collect()
            tracemalloc.start()
            try:
                result = match_lines_preserving_unowned_context(
                    source_lines,
                    target_lines,
                    LineRanges.from_ranges(
                        [(filler_count + 1, filler_count + 2)]
                    ),
                )
                try:
                    _current_heap, peak_heap = tracemalloc.get_traced_memory()
                    assert result.corrected
                finally:
                    if result.owned:
                        result.mapping.close()
            finally:
                tracemalloc.stop()
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _HEAP_GROWTH_TOLERANCE


def test_recorded_boundary_correction_avoids_line_scale_python_heap() -> None:
    """Recorded-run proof and correction state stays out of the Python heap."""
    heap_peaks = []
    for line_count in (512, 8192):
        source = (
            [b"head\n"]
            + [b"\n"] * line_count
            + [b"owned\n"]
            + [b"\n"] * line_count
            + [b"tail\n"]
        )
        target = [b"head\n"] + [b"\n"] * line_count + [b"tail\n"]
        presence_start = line_count + 2
        presence_end = line_count * 2 + 2
        presence_lines = LineRanges.from_ranges(
            [(presence_start, presence_end)]
        )
        reference = BaselineReference(
            after_line=1,
            after_content=b"\n",
            before_line=2,
            before_content=b"tail\n",
            has_before_line=True,
        )
        ownership = BatchOwnership.from_presence_lines(
            [f"{presence_start}-{presence_end}"],
            baseline_references={
                source_line: reference
                for source_line in range(
                    presence_start, presence_end + 1
                )
            },
        )
        ordinary_mapping = allocate_line_mapping(
            len(source), len(target)
        )
        ordinary_mapping.source_to_target[0] = 1
        ordinary_mapping.target_to_source[0] = 1
        for offset in range(line_count):
            source_line = presence_start + 1 + offset
            target_line = 2 + offset
            ordinary_mapping.source_to_target[source_line - 1] = (
                target_line
            )
            ordinary_mapping.target_to_source[target_line - 1] = (
                source_line
            )
        ordinary_mapping.source_to_target[-1] = len(target)
        ordinary_mapping.target_to_source[-1] = len(source)

        context_mapping = allocate_line_mapping(
            len(source), len(target)
        )
        for offset in range(line_count):
            source_line = 2 + offset
            target_line = 2 + offset
            context_mapping.source_to_target[source_line - 1] = (
                target_line
            )
            context_mapping.target_to_source[target_line - 1] = (
                source_line
            )
        context_mapping.source_to_target[-1] = len(target)
        context_mapping.target_to_source[-1] = len(source)

        def context_matcher(*_args, **_kwargs):
            return context_mapping

        try:
            gc.collect()
            tracemalloc.start()
            try:
                result = match_lines_preserving_unowned_context(
                    source,
                    target,
                    presence_lines,
                    ownership=ownership,
                    presence_lines=presence_lines,
                    ordinary_mapping=ordinary_mapping,
                    matcher=context_matcher,
                )
                try:
                    _current_heap, peak_heap = tracemalloc.get_traced_memory()
                    assert result.corrected
                    assert not result.ambiguous
                finally:
                    if result.owned:
                        result.mapping.close()
            finally:
                tracemalloc.stop()
        finally:
            ordinary_mapping.close()
            context_mapping.close()
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + _HEAP_GROWTH_TOLERANCE
