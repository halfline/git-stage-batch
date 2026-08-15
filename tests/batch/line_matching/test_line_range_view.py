"""Tests for indexed line-range views."""

import gc
import tracemalloc

from git_stage_batch.batch.line_matching.line_range_view import LineRangeView
from git_stage_batch.core.buffer import LineBuffer


_LINE_SCALE_HEAP_LIMIT = 64 * 1024


def test_line_range_view_materializes_only_requested_scoped_line():
    """A selected line remains usable after its acquisition scope closes."""
    with LineBuffer.from_bytes(b"zero\none\ntwo\n") as buffer:
        with buffer.acquire_lines() as lines:
            view = LineRangeView(lines, 1, 3)

            selected = view[0]

            assert isinstance(selected, bytes)
            assert selected == b"one\n"

        assert selected == b"one\n"


def test_line_range_view_contiguous_slice_remains_lazy():
    """Contiguous slicing should retain a view instead of copying all lines."""
    view = LineRangeView([b"zero\n", b"one\n", b"two\n"], 0, 3)

    sliced = view[1:]

    assert isinstance(sliced, LineRangeView)
    assert list(sliced) == [b"one\n", b"two\n"]


def test_line_range_view_strided_slice_remains_lazy():
    """Strided slicing must not allocate one Python object per selected line."""
    line_count = 16_384
    view = LineRangeView([b"line\n"] * line_count, 0, line_count)

    gc.collect()
    tracemalloc.start()
    try:
        sliced = view[::2]
        _current_heap, peak_heap = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert isinstance(sliced, LineRangeView)
    assert len(sliced) == line_count // 2
    assert sliced[0] == b"line\n"
    assert sliced[-1] == b"line\n"
    assert peak_heap < _LINE_SCALE_HEAP_LIMIT
