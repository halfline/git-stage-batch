"""Forward origin traversal and single-run projection caching."""

from git_stage_batch.batch.ownership import replacement_origin_cursor
from git_stage_batch.batch.ownership.replacement_line_runs import ReplacementLineRun
from git_stage_batch.batch.ownership.replacement_origins import (
    ProjectedReplacementOrigin,
)
from git_stage_batch.core.buffer import LineBuffer


def test_cursor_visits_each_origin_once_and_reuses_its_projection(monkeypatch):
    calls = []
    consumed = []
    original = replacement_origin_cursor.replacement_unit_origin_for_line_run

    def build_origin(run, **kwargs):
        calls.append(run)
        return original(run, **kwargs)

    monkeypatch.setattr(
        replacement_origin_cursor, "replacement_unit_origin_for_line_run", build_origin
    )
    runs = (
        ReplacementLineRun(1, 1024, 1, 1024),
        ReplacementLineRun(1025, 2048, 1025, 2048),
    )

    def origin_runs():
        for run in runs:
            consumed.append(run)
            yield run

    with LineBuffer.from_chunks(b"old\n" for _ in range(2048)) as lines:
        iterator = origin_runs()
        cursor = replacement_origin_cursor.ReplacementOriginCursor(
            ProjectedReplacementOrigin(iterator, lines),
            iterator,
            None,
        )
        previous = None
        for index in range(1, 2049):
            selected = ReplacementLineRun(index, index, index, index)
            projection = cursor.project(index, index, replacement_run=selected)
            assert projection is not None
            assert projection[1:] == (index, index)
            if index not in (1, 1025):
                assert projection[0] is previous
            previous = projection[0]
        assert consumed == list(runs)
        assert calls == list(runs)
        assert cursor.cached_origin is previous
