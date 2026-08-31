from git_stage_batch.batch.merge.presence_context_alternatives import (
    build_presence_context_alternative_mapping,
    resolve_presence_context_alternative,
)
from git_stage_batch.core.line_selection import LineRanges
SOURCE_LINES = (
    b"base one\n"
    b"request one\n"
    b"base two\n"
    b"request two\n"
    b"completion\n"
    b"tail\n"
    b"base one\n"
    b"request one\n"
    b"base two\n"
    b"request two\n"
    b"tail\n"
).splitlines(keepends=True)
PREDECESSOR_LINES = b"base one\nbase two\ntail\n".splitlines(keepends=True)


def test_presence_context_alternative_maps_exact_claim_elision():
    """The embedded live variant supplies exact context for both claims."""
    alternative = resolve_presence_context_alternative(
        LineRanges.from_specs(["8,10"]),
        SOURCE_LINES,
        PREDECESSOR_LINES,
    )

    assert alternative is not None
    assert (
        alternative.source_context.start.offset,
        alternative.source_context.end.offset,
    ) == (6, 11)
    assert [
        (
            run.source.start.offset,
            run.source.end.offset,
            run.target_gap.offset,
        )
        for run in alternative.runs
    ] == [(7, 8, 1), (9, 10, 2)]

    with build_presence_context_alternative_mapping(
        alternative,
        source_line_count=len(SOURCE_LINES),
        target_line_count=len(PREDECESSOR_LINES),
    ) as mapping:
        assert list(mapping.mapped_line_pairs()) == [(7, 1), (9, 2), (11, 3)]


def test_presence_context_alternative_rejects_wrong_duplicate_coordinates():
    """Payload equality alone cannot move claims into another source version."""
    assert (
        resolve_presence_context_alternative(
            LineRanges.from_specs(["2,4"]),
            SOURCE_LINES,
            PREDECESSOR_LINES,
        )
        is None
    )


def test_presence_context_alternative_requires_unique_enclosing_target():
    """Two exact source envelopes leave the claim placement unresolved."""
    assert (
        resolve_presence_context_alternative(
            LineRanges.from_specs(["2"]),
            [b"same\n", b"claim\n", b"same\n"],
            [b"same\n"],
        )
        is None
    )
