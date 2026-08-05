"""Tests for replacement batch-source helpers."""

import gc
import tracemalloc

from git_stage_batch.batch import replacement as replacement_module
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.replacement import (
    ReplacementBatchView,
    build_replacement_batch_view_from_lines,
)
from git_stage_batch.core.replacement import (
    ReplacementText,
    coerce_replacement_payload,
    replacement_line_bodies,
    replacement_line_chunks,
)


class _RepeatedLines:
    """Large indexed line sequence that reuses one immutable line object."""

    def __init__(self, line_count: int):
        self.line_count = line_count

    def __len__(self) -> int:
        return self.line_count

    def __getitem__(self, index: int) -> bytes:
        if not 0 <= index < self.line_count:
            raise IndexError(index)
        return b"line\n"


def test_build_replacement_batch_view_accepts_non_list_line_sequences(line_sequence):
    """Replacement source construction accepts indexed byte-line sequences."""
    source_lines = line_sequence([b"line1\n", b"old\n", b"line3\n"])
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    view = build_replacement_batch_view_from_lines(
        source_lines,
        ownership,
        "new",
    )

    with view:
        assert isinstance(view, ReplacementBatchView)
        assert view.source_buffer.to_bytes() == b"line1\nnew\nline3\n"
        assert view.ownership.presence_line_set() == {2}


def test_build_replacement_batch_view_returns_named_result(line_sequence):
    """Replacement source construction names generated content and ownership."""
    source_lines = line_sequence([b"line1\n", b"line2\n"])
    ownership = BatchOwnership(
        [],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=[b"old\n"],
            )
        ],
    )

    view = build_replacement_batch_view_from_lines(
        source_lines,
        ownership,
        "new",
    )

    with view:
        assert view.source_buffer.to_bytes() == b"line1\nnew\nline2\n"
        assert view.ownership.presence_line_set() == {2}


def test_replacement_text_can_carry_exact_stdin_bytes():
    payload = coerce_replacement_payload(
        ReplacementText(
            "first\r\nsecond",
            data=b"first\r\nsecond",
            exact=True,
        )
    )

    with replacement_line_chunks(payload) as lines:
        assert list(lines) == [b"first\r\n", b"second"]
    with replacement_line_bodies(payload) as bodies:
        assert list(bodies) == [b"first", b"second"]


def test_legacy_replacement_text_normalizes_line_endings():
    payload = coerce_replacement_payload("first\r\nsecond")

    with replacement_line_chunks(payload) as lines:
        assert list(lines) == [b"first\n", b"second\n"]
    with replacement_line_bodies(payload) as bodies:
        assert list(bodies) == [b"first", b"second"]


def test_replacement_ownership_avoids_line_scale_python_heap(tmp_path):
    """Large selected and inserted ranges stay range-backed on the heap."""

    def peak_for_replacement(
        *,
        source_line_count: int,
        replacement_line_count: int,
    ) -> int:
        source_lines = _RepeatedLines(source_line_count)
        replacement_lines = _RepeatedLines(replacement_line_count)
        ownership = BatchOwnership.from_presence_lines(
            [f"1-{source_line_count}"] if source_line_count else [],
            [],
        )

        gc.collect()
        tracemalloc.start()
        try:
            with replacement_module._build_replacement_batch_view(
                source_lines,
                ownership,
                replacement_lines,
                spool_dir=tmp_path,
            ) as view:
                assert view.ownership.presence_line_set().ranges() == (
                    (1, replacement_line_count),
                )
            _current_heap, peak_heap = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return peak_heap

    small_selected_peak = peak_for_replacement(
        source_line_count=128,
        replacement_line_count=1,
    )
    large_selected_peak = peak_for_replacement(
        source_line_count=32_768,
        replacement_line_count=1,
    )
    small_inserted_peak = peak_for_replacement(
        source_line_count=0,
        replacement_line_count=128,
    )
    large_inserted_peak = peak_for_replacement(
        source_line_count=0,
        replacement_line_count=32_768,
    )

    assert large_selected_peak < small_selected_peak + 128 * 1024
    assert large_inserted_peak < small_inserted_peak + 128 * 1024
