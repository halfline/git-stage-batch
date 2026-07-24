"""Tests for streamed replacement-line run derivation."""

from git_stage_batch.batch.line_matching.comparison import (
    SemanticChangeKind,
    SemanticChangeRun,
)
import git_stage_batch.batch.ownership.replacement_line_runs as runs_module
from git_stage_batch.batch.ownership.replacement_line_runs import (
    ReplacementLineRun,
    stream_replacement_line_runs_from_lines,
)


def test_stream_replacement_line_runs_closes_semantic_runs(monkeypatch):
    """Closing replacement runs must propagate to streamed comparison state."""

    class ClosableSemanticRuns:
        def __init__(self):
            self._runs = iter((
                SemanticChangeRun(
                    kind=SemanticChangeKind.REPLACEMENT,
                    source_start=1,
                    source_end=1,
                    target_start=1,
                    target_end=1,
                ),
            ))
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._runs)

        def close(self):
            self.closed = True

    semantic_runs = ClosableSemanticRuns()
    monkeypatch.setattr(
        runs_module,
        "stream_semantic_change_runs",
        lambda *_args, **_kwargs: semantic_runs,
    )
    replacement_runs = stream_replacement_line_runs_from_lines(
        old_file_lines=[b"old\n"],
        new_file_lines=[b"new\n"],
    )

    assert next(replacement_runs) == ReplacementLineRun(1, 1, 1, 1)
    assert semantic_runs.closed is False

    replacement_runs.close()

    assert semantic_runs.closed is True
