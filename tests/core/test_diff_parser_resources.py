"""Heap and input-consumption bounds for streamed diff parsing."""

import gc
import tracemalloc

from git_stage_batch.core.diff_parser import acquire_unified_diff
from git_stage_batch.core.models import SingleHunkPatch
from git_stage_batch.core.diff_stream import DiffLineCursor


def test_cursor_peek_and_pushback_consume_each_input_line_once():
    consumed = []
    closed = []

    def lines():
        try:
            for line in (b"first\n", b"second\n", b"third\n"):
                consumed.append(line)
                yield line
        finally:
            closed.append(True)

    cursor = DiffLineCursor(lines())
    assert cursor.peek_line() == cursor.peek_line() == b"first\n"
    assert consumed == [b"first\n"]
    assert cursor.next_line() == b"first\n"
    second = cursor.next_line()
    cursor.push_back(second)
    assert cursor.peek_line() == cursor.next_line() == b"second\n"
    assert consumed == [b"first\n", b"second\n"]
    cursor.close()
    assert closed == [True]


def test_headerless_metadata_hunk_uses_bounded_python_heap():
    """Gitlink detection must not materialize every text row in a Python list."""
    peaks = []
    for line_count in (2048, 16384):
        consumed = 0

        def lines():
            nonlocal consumed
            yield b"diff --git a/file b/file\n"
            yield b"--- a/file\n"
            yield b"+++ b/file\n"
            yield f"@@ -0,0 +1,{line_count} @@\n".encode()
            for index in range(line_count):
                consumed += 1
                yield f"+unique content {index}\n".encode()

        gc.collect()
        tracemalloc.start()
        try:
            with acquire_unified_diff(lines()) as patches:
                patch = next(patches)
                assert isinstance(patch, SingleHunkPatch)
                assert len(patch.lines) == line_count + 3
                assert patch.lines[-1] == f"+unique content {line_count - 1}\n".encode()
                _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert consumed == line_count
        peaks.append(peak)
    assert peaks[1] < peaks[0] + 32 * 1024
