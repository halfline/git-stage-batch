"""Tests for replacement-selection command helpers."""

import gc
import tracemalloc

import pytest

from git_stage_batch.batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
)
from git_stage_batch.commands.selection.replacement_selection import (
    expand_replacement_selection_ids,
    expand_replacement_selection_ids_with_explicit_span_status,
    require_contiguous_display_selection,
)
from git_stage_batch.commands.selection.discard_line_replacement import (
    _contiguous_selected_addition_count,
    _expand_parent_through_relocated_prefix_context,
    _matching_discard_prefix_context_count,
    _selected_additions_cover_working_span,
)
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
    assert _matching_discard_prefix_context_count(
        [b"selected", b"}", b"live", b"}"],
        [b"}\n", b"after\n"],
        prefix_count=1,
        working_suffix_start=0,
    ) == 1


def test_discard_prefix_context_leaves_final_context_copy_unclaimed():
    """A lone copied close can remain as the unchanged working suffix."""
    assert _matching_discard_prefix_context_count(
        [b"selected", b"}"],
        [b"}\n", b"after\n"],
        prefix_count=1,
        working_suffix_start=0,
    ) == 0


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
        baseline.extend(
            f"baseline-{index}\n".encode() for index in range(line_count)
        )
        target.extend(
            f"target-{index}\n".encode() for index in range(line_count)
        )
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
