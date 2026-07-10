"""Tests for buffer-backed candidate preview rendering."""


from git_stage_batch.core.buffer import LineBuffer
from git_stage_batch.output.candidate_preview_diff import (
    print_candidate_buffer_diff,
    render_candidate_buffer_diff,
)


class _IndexGuardedLineBuffer(LineBuffer):
    """Line buffer that requires consumers to use scoped acquisition."""

    def __getitem__(self, index):
        raise AssertionError("public line indexing should not be used")


def _guarded_buffer(content: bytes) -> _IndexGuardedLineBuffer:
    return _IndexGuardedLineBuffer.from_bytes(content)


def test_candidate_diff_matches_through_acquired_line_views():
    with (
        _guarded_buffer(b"old\n") as before,
        _guarded_buffer(b"new\n") as after,
    ):
        rendered = render_candidate_buffer_diff(
            "file.txt",
            before,
            after,
            label_before="a",
            label_after="b",
            context_lines=3,
        )

    assert rendered == (
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_candidate_diff_printing_consumes_buffered_hunks(capsys):
    with (
        _guarded_buffer(b"old\n") as before,
        _guarded_buffer(b"new\n") as after,
    ):
        assert print_candidate_buffer_diff(
            "file.txt",
            before,
            after,
            context_lines=3,
            ambiguity_target_line_range=None,
        ) is True

    output = capsys.readouterr().out
    assert "-old" in output
    assert "+new" in output
