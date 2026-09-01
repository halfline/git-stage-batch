"""Tests for replacement-selection command helpers."""

import gc
import tracemalloc

import pytest

from git_stage_batch.batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
)
from git_stage_batch.commands.selection.replacement_selection import (
    expand_addition_selection_to_keep_repeated_context,
    expand_replacement_selection_ids,
    expand_replacement_selection_ids_with_explicit_span_status,
    require_contiguous_display_selection,
)
from git_stage_batch.commands.selection.discard_line_replacement import (
    _contiguous_selected_addition_count,
    _expand_parent_through_relocated_prefix_context,
    _requires_explicit_added_side_alternative,
    _matching_discard_prefix_context_count,
    _matching_baseline_prefix_context_count,
    _replacement_payload_retains_selected_addition,
    _verified_explicit_alternative_end,
    _selected_additions_cover_working_span,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.exceptions import CommandError


def test_contiguous_display_selection_accepts_adjacent_ids():
    """Adjacent selected display IDs should pass replacement validation."""
    require_contiguous_display_selection({2, 3, 4})


def test_contiguous_display_selection_rejects_gapped_ids():
    """Gapped selected display IDs should fail replacement validation."""
    with pytest.raises(CommandError) as exc_info:
        require_contiguous_display_selection({2, 4})

    assert "Replacement selection must be one contiguous line range." in (
        exc_info.value.message
    )


def test_addition_selection_keeps_repeated_context_before_it():
    """Selected new text keeps the copied lines that place it after a block."""
    line_changes = LineLevelChange(
        path="driver.c",
        header=HunkHeader(1, 4, 1, 9),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 1, text_bytes=b"new"),
            LineEntry(3, "+", None, 2, text_bytes=b"}"),
            LineEntry(4, "+", None, 3, text_bytes=b""),
            LineEntry(5, "+", None, 4, text_bytes=b"new_block"),
            LineEntry(6, "+", None, 5, text_bytes=b"{"),
            LineEntry(None, " ", 2, 6, text_bytes=b"}"),
            LineEntry(None, " ", 3, 7, text_bytes=b""),
            LineEntry(None, " ", 4, 8, text_bytes=b"tail"),
        ],
    )
    assert set(
        expand_addition_selection_to_keep_repeated_context(
            line_changes,
            {5, 6},
        )
    ) == {3, 4, 5, 6}


def test_inner_addition_selection_does_not_copy_later_context():
    """An unselected new line after the selection keeps the old anchor reusable."""
    line_changes = LineLevelChange(
        path="notes.txt",
        header=HunkHeader(1, 2, 1, 6),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b""),
            LineEntry(2, "+", None, 2, text_bytes=b"selected"),
            LineEntry(3, "+", None, 3, text_bytes=b"later"),
            LineEntry(None, " ", 1, 4, text_bytes=b""),
            LineEntry(None, " ", 2, 5, text_bytes=b"tail"),
        ],
    )
    selected_ids = {2}
    assert (
        expand_addition_selection_to_keep_repeated_context(
            line_changes,
            selected_ids,
        )
        is selected_ids
    )


def test_repeated_context_expansion_avoids_line_scale_python_heap():
    """Large copied spans stay range-backed while their match uses mapped storage."""
    heap_peaks = []
    for line_count in (1024, 8192):
        lines = [
            LineEntry(
                line_id,
                "+",
                None,
                line_id,
                text_bytes=b"}",
            )
            for line_id in range(1, line_count + 1)
        ]
        selected_id = line_count + 1
        lines.append(
            LineEntry(
                selected_id,
                "+",
                None,
                selected_id,
                text_bytes=b"selected",
            )
        )
        lines.extend(
            LineEntry(
                None,
                " ",
                context_offset,
                selected_id + context_offset,
                text_bytes=b"}",
            )
            for context_offset in range(1, line_count + 1)
        )
        line_changes = LineLevelChange(
            path="driver.c",
            header=HunkHeader(1, line_count, 1, len(lines)),
            lines=lines,
        )

        gc.collect()
        tracemalloc.start()
        try:
            expanded = expand_addition_selection_to_keep_repeated_context(
                line_changes,
                {selected_id},
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert len(expanded) == line_count + 1
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 32 * 1024


@pytest.mark.parametrize(
    "requested_ids",
    [{3}, {3, 4}, {1, 2}],
)
def test_replacement_selection_expands_both_complete_sides(requested_ids):
    """Selecting either side of a replacement includes the full mixed run."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 2, 1, 2),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "-", 2, None, text_bytes=b"old-b"),
            LineEntry(3, "+", None, 1, text_bytes=b"new-a"),
            LineEntry(4, "+", None, 2, text_bytes=b"new-b"),
        ],
    )

    assert expand_replacement_selection_ids(line_changes, requested_ids) == {
        1,
        2,
        3,
        4,
    }


def test_replacement_selection_leaves_surplus_additions_outside_core():
    """An insertion following a one-for-one replacement remains independently selectable."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 2),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 1, text_bytes=b"new"),
            LineEntry(3, "+", None, 2, text_bytes=b"extra"),
        ],
    )

    assert expand_replacement_selection_ids(line_changes, {2}) == {1, 2}
    assert expand_replacement_selection_ids(line_changes, {3}) == {3}


def test_replacement_text_can_keep_an_explicit_old_side_span():
    """An explicit old-side edit need not absorb neighboring removals."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 3, 1, 1),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"replace"),
            LineEntry(2, "-", 2, None, text_bytes=b"keep-a"),
            LineEntry(3, "-", 3, None, text_bytes=b"keep-b"),
            LineEntry(4, "+", None, 1, text_bytes=b"working"),
        ],
    )

    assert expand_replacement_selection_ids(
        line_changes,
        {1},
        preserve_explicit_deletion_span=True,
    ) == {1}


def test_replacement_selection_keeps_explicit_partial_addition_prefix():
    """A complete old side plus an explicit new prefix should remain exact."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 3, 1, 4),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "-", 2, None, text_bytes=b"old-b"),
            LineEntry(3, "-", 3, None, text_bytes=b"old-c"),
            LineEntry(4, "+", None, 1, text_bytes=b"batch-a"),
            LineEntry(5, "+", None, 2, text_bytes=b"batch-b"),
            LineEntry(6, "+", None, 3, text_bytes=b"live-a"),
            LineEntry(7, "+", None, 4, text_bytes=b"live-b"),
        ],
    )

    assert expand_replacement_selection_ids(
        line_changes,
        {1, 2, 3, 4, 5},
        preserve_partial_addition_prefix=True,
    ) == {
        1,
        2,
        3,
        4,
        5,
    }

    assert expand_replacement_selection_ids(
        line_changes,
        {1, 2, 3, 4, 5},
    ) == {1, 2, 3, 4, 5, 6}


def test_replacement_selection_keeps_explicit_inner_addition_span():
    """An explicit inner new span should not absorb its mixed replacement."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 3, 1, 4),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "-", 2, None, text_bytes=b"old-b"),
            LineEntry(3, "-", 3, None, text_bytes=b"old-c"),
            LineEntry(4, "+", None, 1, text_bytes=b"new-a"),
            LineEntry(5, "+", None, 2, text_bytes=b"new-b"),
            LineEntry(6, "+", None, 3, text_bytes=b"new-c"),
            LineEntry(7, "+", None, 4, text_bytes=b"new-d"),
        ],
    )

    assert expand_replacement_selection_ids(
        line_changes,
        {5},
        preserve_explicit_addition_span=True,
    ) == {5}
    assert expand_replacement_selection_ids(
        line_changes,
        {5, 6},
        preserve_explicit_addition_span=True,
    ) == {5, 6}
    assert expand_replacement_selection_ids(
        line_changes,
        {4, 6},
        preserve_explicit_addition_span=True,
    ) == {1, 2, 3, 4, 5, 6}

    assert expand_replacement_selection_ids(line_changes, {5}) == {
        1,
        2,
        3,
        4,
        5,
        6,
    }

    selected_ids = {5}
    effective_ids, preserved_explicit_addition_span = (
        expand_replacement_selection_ids_with_explicit_span_status(
            line_changes,
            selected_ids,
        )
    )
    assert effective_ids is selected_ids
    assert preserved_explicit_addition_span


def test_explicit_inner_addition_span_avoids_line_scale_python_heap():
    """Preserving a large inner span should retain only scalar scan state."""
    heap_peaks = []
    for line_count in (1024, 8192):
        lines = [
            LineEntry(
                line_id,
                "-",
                line_id,
                None,
                text_bytes=b"old",
            )
            for line_id in range(1, line_count + 1)
        ]
        lines.extend(
            LineEntry(
                line_count + offset,
                "+",
                None,
                offset,
                text_bytes=b"new",
            )
            for offset in range(1, line_count * 2 + 1)
        )
        line_changes = LineLevelChange(
            path="test.txt",
            header=HunkHeader(1, line_count, 1, line_count * 2),
            lines=lines,
        )
        selected_ids = set(
            range(
                line_count + line_count // 4,
                line_count + line_count * 3 // 4,
            )
        )

        gc.collect()
        tracemalloc.start()
        try:
            effective_ids, preserved_explicit_addition_span = (
                expand_replacement_selection_ids_with_explicit_span_status(
                    line_changes,
                    selected_ids,
                )
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert effective_ids is selected_ids
        assert preserved_explicit_addition_span
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 16 * 1024


def test_replacement_selection_refuses_unavailable_opposite_side():
    """A skipped row cannot be silently omitted from a selected replacement."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 1),
        lines=[
            LineEntry(None, "-", 1, None, text_bytes=b"skipped-old"),
            LineEntry(2, "+", None, 1, text_bytes=b"selected-new"),
        ],
    )

    for preserve_explicit_addition_span in (False, True):
        with pytest.raises(
            CommandError,
            match="changed line in the run is unavailable",
        ):
            expand_replacement_selection_ids(
                line_changes,
                {2},
                preserve_explicit_addition_span=preserve_explicit_addition_span,
            )


def test_explicit_addition_span_defers_unavailable_same_side_peer():
    """A hidden unselected addition should matter only if fallback is needed."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 3, 1, 3),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "-", 2, None, text_bytes=b"old-b"),
            LineEntry(3, "-", 3, None, text_bytes=b"old-c"),
            LineEntry(4, "+", None, 1, text_bytes=b"new-a"),
            LineEntry(5, "+", None, 2, text_bytes=b"new-b"),
            LineEntry(None, "+", None, 3, text_bytes=b"hidden-new-c"),
        ],
    )
    selected_ids = {5}

    effective_ids, preserved_explicit_addition_span = (
        expand_replacement_selection_ids_with_explicit_span_status(
            line_changes,
            selected_ids,
        )
    )
    assert effective_ids is selected_ids
    assert preserved_explicit_addition_span

    with pytest.raises(
        CommandError,
        match="changed line in the run is unavailable",
    ):
        expand_replacement_selection_ids(line_changes, selected_ids)


def test_replacement_selection_expands_each_selected_disjoint_run():
    """File-scoped selections may cross context and still expand each replacement."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 4, 1, 4),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old-a"),
            LineEntry(2, "+", None, 1, text_bytes=b"new-a"),
            LineEntry(None, " ", 2, 2, text_bytes=b"context"),
            LineEntry(3, "-", 3, None, text_bytes=b"old-b"),
            LineEntry(4, "-", 4, None, text_bytes=b"old-c"),
            LineEntry(5, "+", None, 3, text_bytes=b"new-b"),
            LineEntry(6, "+", None, 4, text_bytes=b"new-c"),
        ],
    )

    assert expand_replacement_selection_ids(line_changes, {2, 5}) == {
        1,
        2,
        3,
        4,
        5,
        6,
    }

    assert expand_replacement_selection_ids(
        line_changes,
        {2, 5},
        preserve_explicit_addition_span=True,
    ) == {
        1,
        2,
        3,
        4,
        5,
        6,
    }


def test_exact_addition_prefix_requires_contiguous_working_lines():
    """Disjoint additions cannot activate exact-span removal."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 3),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"first"),
            LineEntry(None, " ", 1, 2, text_bytes=b"middle"),
            LineEntry(2, "+", None, 3, text_bytes=b"last"),
        ],
    )

    assert _contiguous_selected_addition_count(line_changes, {1, 2}) is None


def test_exact_addition_prefix_counts_one_contiguous_working_span():
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 2),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"first"),
            LineEntry(2, "+", None, 2, text_bytes=b"last"),
        ],
    )

    assert _contiguous_selected_addition_count(line_changes, {1, 2}) == 2


@pytest.mark.parametrize(
    (
        "replacement_lines",
        "baseline_file_exists",
        "has_deletion_peer",
        "destination_has_file",
        "no_edge_overlap",
        "expected",
    ),
    [
        ([b"one"], True, True, False, False, True),
        ([b"changed", b"two"], False, False, False, False, True),
        ([b"changed", b"two"], True, False, True, False, True),
        ([b"on", b"two"], True, False, True, False, False),
        ([b"on", b"two"], True, False, False, True, True),
        ([b"changed", b"two"], False, True, True, False, False),
        ([b"one", b"two"], False, False, True, False, False),
        ([b"changed", b"two", b"three"], False, False, False, False, True),
        ([b"one", b"two", b"three"], False, False, False, False, False),
        ([], False, False, False, False, True),
    ],
)
def test_explicit_added_side_alternative_geometry(
    replacement_lines,
    baseline_file_exists,
    has_deletion_peer,
    destination_has_file,
    no_edge_overlap,
    expected,
):
    """Only unambiguous added-side transforms become explicit alternatives."""
    assert (
        _requires_explicit_added_side_alternative(
            replacement_lines,
            [b"one\n", b"two\r\n"],
            working_start=0,
            working_end=2,
            baseline_file_exists=baseline_file_exists,
            has_deletion_peer=has_deletion_peer,
            destination_has_file=destination_has_file,
            no_edge_overlap=no_edge_overlap,
        )
        is expected
    )


def test_replacement_additions_cover_nested_working_span():
    """Adjacent outer changes do not hide a complete selected replacement."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 4, 1, 5),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"outer old"),
            LineEntry(2, "+", None, 1, text_bytes=b"outer new"),
            LineEntry(3, "-", 2, None, text_bytes=b"selected old one"),
            LineEntry(4, "-", 3, None, text_bytes=b"selected old two"),
            LineEntry(5, "+", None, 2, text_bytes=b"selected new one"),
            LineEntry(6, "+", None, 3, text_bytes=b"selected new two"),
            LineEntry(7, "+", None, 5, text_bytes=b"later change"),
        ],
    )

    assert _selected_additions_cover_working_span(
        line_changes,
        {3, 4, 5, 6},
        replacement_start=1,
        replacement_end=3,
    )


def test_replacement_additions_do_not_cover_unchanged_gap():
    """A gapped working span cannot become an explicit owned prefix."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 2, 1, 3),
        lines=[
            LineEntry(1, "-", 1, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 1, text_bytes=b"first"),
            LineEntry(None, " ", 2, 2, text_bytes=b"stable"),
            LineEntry(3, "+", None, 3, text_bytes=b"last"),
        ],
    )

    assert not _selected_additions_cover_working_span(
        line_changes,
        {1, 2, 3},
        replacement_start=0,
        replacement_end=3,
    )


def test_replacement_additions_reject_interleaved_deletion():
    """A malformed add/delete ordering is not a semantic prefix."""
    line_changes = LineLevelChange(
        path="test.txt",
        header=HunkHeader(1, 1, 1, 1),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"new"),
            LineEntry(2, "-", 1, None, text_bytes=b"old"),
        ],
    )

    assert not _selected_additions_cover_working_span(
        line_changes,
        {1, 2},
        replacement_start=0,
        replacement_end=1,
    )


def test_discard_prefix_context_counts_adjacent_closing_delimiter():
    """A copied close before the live alternative belongs to the prefix."""
    assert (
        _matching_discard_prefix_context_count(
            [b"selected", b"}", b"live", b"}"],
            [b"}\n", b"after\n"],
            prefix_count=1,
            working_suffix_start=0,
        )
        == 1
    )


def test_discard_prefix_context_leaves_final_context_copy_unclaimed():
    """A lone copied close can remain as the unchanged working suffix."""
    assert (
        _matching_discard_prefix_context_count(
            [b"selected", b"}"],
            [b"}\n", b"after\n"],
            prefix_count=1,
            working_suffix_start=0,
        )
        == 0
    )


def test_discard_prefix_context_accepts_tracked_content_before_alternative():
    """A tracked transform may copy unchanged content into its owned prefix."""
    assert (
        _matching_discard_prefix_context_count(
            [b"selected", b"body", b"", b"done", b"live"],
            [b"body\n", b"\n", b"done\n", b"after\n"],
            prefix_count=1,
            working_suffix_start=0,
            allow_content=True,
        )
        == 3
    )


def test_baseline_prefix_context_requires_unchanged_parent_lines():
    """Only copied context shared with the baseline expands a tracked parent."""
    assert (
        _matching_baseline_prefix_context_count(
            [b"old\n", b"shared\n", b"different\n"],
            [b"new\n", b"shared\n", b"working\n"],
            baseline_suffix_start=1,
            working_suffix_start=1,
            maximum_count=2,
        )
        == 1
    )


def test_explicit_alternative_extends_through_verified_shared_tail():
    """A trimmed live alternative includes its contiguous payload suffix."""
    assert (
        _verified_explicit_alternative_end(
            selection_lines=[
                b"saved\n",
                b"old changed\n",
                b"shared\n",
                b"after\n",
            ],
            payload_lines=[b"saved", b"old changed", b"shared"],
            owned_prefix_count=1,
            alternative_start=2,
            fallback_end=2,
        )
        == 3
    )


def test_discard_prefix_context_avoids_line_scale_python_heap():
    """Copied-context discovery should stream through existing line storage."""
    heap_peaks = []
    for line_count in (1024, 8192):
        payload = [b"selected", *([b"}"] * line_count), b"live"]
        working = [b"}\n"] * line_count

        gc.collect()
        tracemalloc.start()
        try:
            matched = _matching_discard_prefix_context_count(
                payload,
                working,
                prefix_count=1,
                working_suffix_start=0,
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert matched == line_count
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 32 * 1024


def test_retained_addition_detection_avoids_line_scale_python_heap():
    """Requested-overlap discovery should keep its indexes in mapped storage."""
    line_changes = LineLevelChange(
        path="tracked.txt",
        header=HunkHeader(1, 1, 1, 1),
        lines=[
            LineEntry(1, "+", None, 1, text_bytes=b"retained-target"),
        ],
    )
    heap_peaks = []
    for line_count in (1024, 8192):
        with (
            LineBuffer.from_chunks(
                (
                    b"retained-target\n"
                    if index == line_count - 1
                    else f"replacement-{index}\n".encode()
                    for index in range(line_count)
                )
            ) as replacement_lines,
            LineBuffer.from_chunks(
                f"baseline-{index}\n".encode() for index in range(line_count)
            ) as baseline_lines,
        ):
            gc.collect()
            tracemalloc.start()
            try:
                retains_addition = _replacement_payload_retains_selected_addition(
                    line_changes,
                    {1},
                    replacement_lines,
                    baseline_lines,
                )
                _current_heap, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        assert retains_addition
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 32 * 1024


def test_replacement_parent_includes_delimiter_relocated_after_prefix():
    """An owned close must replace its baseline copy despite a later match."""
    baseline = b"""prefix
signature
{
	old body;
	}
}
suffix
""".splitlines(keepends=True)
    target = b"""prefix
signature
{
	new body;
}
EXPORT(selector);
validator
{
	}
}
suffix
""".splitlines(keepends=True)
    prefix = target[3:6]

    parent = _expand_parent_through_relocated_prefix_context(
        ReplacementLineRun(4, 4, 4, 8),
        baseline_lines=baseline,
        original_working_lines=target,
        rewritten_prefix_lines=prefix,
    )

    assert parent == ReplacementLineRun(4, 6, 4, 10)


def test_replacement_parent_stops_at_contentful_context():
    """A later duplicate cannot pull unrelated baseline content into a parent."""
    baseline = [
        b"prefix\n",
        b"old body\n",
        b"stable boundary\n",
        b"}\n",
        b"suffix\n",
    ]
    target = [
        b"prefix\n",
        b"new body\n",
        b"}\n",
        b"stable boundary\n",
        b"}\n",
        b"suffix\n",
    ]
    original = ReplacementLineRun(2, 2, 2, 3)

    parent = _expand_parent_through_relocated_prefix_context(
        original,
        baseline_lines=baseline,
        original_working_lines=target,
        rewritten_prefix_lines=target[1:3],
    )

    assert parent == original


def test_replacement_parent_does_not_expand_for_relocated_blank_line():
    """Whitespace alone is not enough evidence to absorb baseline context."""
    baseline = [b"prefix\n", b"old body\n", b"\n", b"suffix\n"]
    target = [
        b"prefix\n",
        b"new body\n",
        b"\n",
        b"adjacent block\n",
        b"\n",
        b"suffix\n",
    ]
    original = ReplacementLineRun(2, 2, 2, 4)

    parent = _expand_parent_through_relocated_prefix_context(
        original,
        baseline_lines=baseline,
        original_working_lines=target,
        rewritten_prefix_lines=target[1:3],
    )

    assert parent == original


def test_relocated_prefix_context_avoids_line_scale_python_heap():
    """Context recovery should keep its file-sized state in mapped storage."""
    heap_peaks = []
    for line_count in (1024, 8192):
        baseline = [b"prefix\n", b"old body\n"]
        target = [b"prefix\n", b"new body\n"]
        baseline.extend(f"baseline-{index}\n".encode() for index in range(line_count))
        target.extend(f"target-{index}\n".encode() for index in range(line_count))
        baseline.append(b"suffix\n")
        target.append(b"suffix\n")

        gc.collect()
        tracemalloc.start()
        try:
            parent = _expand_parent_through_relocated_prefix_context(
                ReplacementLineRun(2, 2, 2, 2),
                baseline_lines=baseline,
                original_working_lines=target,
                rewritten_prefix_lines=target[1:2],
            )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert parent == ReplacementLineRun(2, 2, 2, 2)
        heap_peaks.append(peak_heap)

    small_peak, large_peak = heap_peaks
    assert large_peak < small_peak + 32 * 1024
