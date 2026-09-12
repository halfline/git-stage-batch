"""Resource bounds for replacement policy over existing selection storage."""

import gc
import tracemalloc
from contextlib import ExitStack

import pytest

from git_stage_batch.commands.selection.discard_replacement_models import (
    _ReplacementBufferMode,
    _ReplacementDestinationState,
)
from git_stage_batch.commands.selection.discard_replacement_policy import (
    _resolve_replacement_decision,
)
from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.core.replacement import ReplacementPayload


@pytest.mark.parametrize("no_edge_overlap", [False, True])
def test_retained_prefix_policy_has_bounded_heap_and_linear_line_reads(
    monkeypatch, no_edge_overlap,
):
    """Growing a saved prefix must not copy its rows or repeatedly rescan it."""
    original_getitem = LineBuffer.__getitem__
    reads = 0

    def counted_getitem(buffer, index):
        nonlocal reads
        reads += 1
        return original_getitem(buffer, index)

    monkeypatch.setattr(LineBuffer, "__getitem__", counted_getitem)
    heap_peaks = []
    line_reads = []
    for line_count in (1024, 8192):
        selected_ids = set(range(1, line_count + 1))
        line_changes = LineLevelChange(
            path="tracked.txt",
            header=HunkHeader(1, 0, 1, line_count),
            lines=[
                LineEntry(index, "+", None, index, text_bytes=b"saved")
                for index in range(1, line_count + 1)
            ],
        )
        payload = ReplacementPayload(b"saved\n" * line_count + b"live\n")
        with (
            LineBuffer.from_bytes(b"saved\n" * line_count) as working_lines,
            LineBuffer.from_bytes(b"") as baseline_lines,
            ExitStack() as source_stack,
        ):
            gc.collect()
            reads = 0
            tracemalloc.start()
            try:
                decision = _resolve_replacement_decision(
                    line_changes,
                    selected_ids,
                    selected_ids,
                    payload,
                    working_lines,
                    baseline_lines,
                    source_stack,
                    destination=_ReplacementDestinationState("saved", False),
                    baseline_file_exists=True,
                    uses_explicit_addition_span=True,
                    has_explicit_addition_subspan=False,
                    requested_run_has_deletion=False,
                    no_edge_overlap=no_edge_overlap,
                )
                _, peak_heap = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

        assert decision.effective_ids is selected_ids
        assert decision.buffer_mode is _ReplacementBufferMode.SELECTED_LINES
        assert decision.saved_prefix is not None
        assert decision.saved_prefix.line_count == line_count
        heap_peaks.append(peak_heap)
        line_reads.append(reads)

    small_peak, large_peak = heap_peaks
    small_reads, large_reads = line_reads
    assert large_peak < small_peak + 32 * 1024
    assert 0 < small_reads < large_reads
    assert large_reads <= small_reads * 9
