"""Complete-source replay must prove which formatting can be restored."""

from itertools import chain
import tracemalloc

import pytest

from git_stage_batch.batch.merge.baseline_replay import try_replay_complete_source
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.core.buffer import LineBuffer


def _insertion_ownership(context_count):
    return BatchOwnership.from_presence_lines(
        [str(context_count + 2)],
        baseline_references={
            context_count + 2: BaselineReference(
                after_line=context_count + 1,
                after_content=b"",
                before_line=context_count + 2,
                before_content=b"}",
                has_before_line=True,
            )
        },
    )


@pytest.mark.parametrize(
    "target", [b"changed\n}\n", b"context\n", b"context\n}\nextra\n"]
)
def test_separator_replay_does_not_ignore_other_edits(target):
    with (
        LineBuffer.from_bytes(b"context\n\n}\n") as baseline,
        LineBuffer.from_bytes(b"context\n\nnew\n}\n") as source,
        LineBuffer.from_bytes(target) as working,
    ):
        assert (
            try_replay_complete_source(
                baseline, source, working, _insertion_ownership(1)
            )
            is None
        )


def test_separator_replay_does_not_restore_an_unrelated_blank_line():
    with (
        LineBuffer.from_bytes(b"\ncontext\n\n}\n") as baseline,
        LineBuffer.from_bytes(b"\ncontext\n\nnew\n}\n") as source,
        LineBuffer.from_bytes(b"context\n}\n") as working,
    ):
        assert (
            try_replay_complete_source(
                baseline, source, working, _insertion_ownership(2)
            )
            is None
        )


def test_separator_replay_uses_bounded_python_heap():
    peaks = []
    for count in (4096, 65536):
        with (
            LineBuffer.from_chunks(
                chain((b"context\n" for _ in range(count)), (b"\n", b"}\n"))
            ) as baseline,
            LineBuffer.from_chunks(
                chain((b"context\n" for _ in range(count)), (b"\n", b"new\n", b"}\n"))
            ) as source,
            LineBuffer.from_chunks(
                chain((b"context\n" for _ in range(count)), (b"}\n",))
            ) as target,
        ):
            ownership = _insertion_ownership(count)
            tracemalloc.start()
            try:
                result = try_replay_complete_source(baseline, source, target, ownership)
                assert result is not None
                with result:
                    assert len(result) == count + 3
                    assert result[count + 1] == b"new\n"
                peaks.append(tracemalloc.get_traced_memory()[1])
            finally:
                tracemalloc.stop()
    assert peaks[1] < peaks[0] + 256 * 1024
