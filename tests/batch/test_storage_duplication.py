"""Tests for avoiding duplication in realized batch content."""

from __future__ import annotations

import git_stage_batch.batch.realized_file_content as realized_file_content
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.batch.realized_file_content import (
    build_realized_buffer_from_lines,
)
from git_stage_batch.core.buffer import LineBuffer


def _build_realized_content_from_bytes(
    base_content: bytes,
    batch_source_content: bytes,
    ownership: BatchOwnership,
) -> bytes:
    with (
        LineBuffer.from_bytes(base_content) as base_lines,
        LineBuffer.from_bytes(batch_source_content) as batch_source_lines,
        build_realized_buffer_from_lines(
            base_lines,
            batch_source_lines,
            ownership,
        ) as result,
    ):
        return result.to_bytes()


def test_build_realized_content_routes_storage_to_invocation_spool(
    tmp_path,
    monkeypatch,
):
    """Stored-file matching and realization use the caller's scratch path."""
    spool_dir = tmp_path / "scratch"
    spool_dir.mkdir()
    matching_spools = []
    realization_spools = []
    original_match_lines = realized_file_content._match_lines
    original_satisfy_constraints = realized_file_content.satisfy_constraints

    def record_match(*args, **kwargs):
        matching_spools.append(kwargs.get("spool_dir"))
        return original_match_lines(*args, **kwargs)

    def record_realization(*args, **kwargs):
        realization_spools.append(kwargs.get("spool_dir"))
        return original_satisfy_constraints(*args, **kwargs)

    monkeypatch.setattr(realized_file_content, "_match_lines", record_match)
    monkeypatch.setattr(
        realized_file_content,
        "satisfy_constraints",
        record_realization,
    )

    ownership = BatchOwnership.from_presence_lines(["2"], [])
    with (
        LineBuffer.from_bytes(b"one\n") as base_lines,
        LineBuffer.from_bytes(b"one\ninserted\n") as source_lines,
        build_realized_buffer_from_lines(
            base_lines,
            source_lines,
            ownership,
            spool_dir=spool_dir,
        ) as result,
    ):
        assert result.to_bytes() == b"one\ninserted\n"

    assert matching_spools == [spool_dir]
    assert realization_spools == [spool_dir]


def test_build_realized_content_no_duplication_when_claiming_moved_line():
    """Test that claiming a moved line doesn't duplicate it in realized content.

    Scenario: A line exists in base and is also added elsewhere in batch source.
    When we claim the added instance, it should appear once, not twice.
    """
    # Base has line "X" at position 2
    base_content = b"A\nX\nB\n"

    # Batch source moved "X" to position 1 and kept it at position 2
    # (or added a duplicate "X")
    batch_source_content = b"X\nA\nX\nB\n"

    # Claim line 1 (the moved/added X)
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    result_lines = result.decode().split('\n')

    # Should have: X (claimed), A (base), X (base), B (base)
    # The base copy of X should not be repeated.
    print(f"Result lines: {result_lines}")
    print(f"Result: {result}")

    # Count occurrences of "X"
    x_count = result_lines.count("X")
    assert x_count == 2, f"Expected 'X' to appear 2 times, but got {x_count} times: {result_lines}"


def test_build_realized_content_uses_exact_split_replacement_reference():
    """Repeated parent lines should retain the unselected sibling."""
    ownership = BatchOwnership.from_presence_lines(
        ["3"],
        [
            AbsenceClaim(
                anchor_line=2,
                content_lines=[b"same\n"],
                baseline_reference=BaselineReference(
                    after_line=1,
                    after_content=b"head",
                    before_line=3,
                    before_content=b"same",
                    has_before_line=True,
                ),
            )
        ],
        replacement_units=[
            ReplacementUnit(
                presence_lines=["3"],
                deletion_indices=[0],
                origin=ReplacementUnitOrigin(
                    old_start=2,
                    old_end=3,
                    new_start=2,
                    new_end=3,
                    baseline_reference=BaselineReference(
                        after_line=1,
                        after_content=b"head",
                        before_line=4,
                        before_content=b"tail",
                        has_before_line=True,
                    ),
                ),
            )
        ],
    )

    result = _build_realized_content_from_bytes(
        b"head\nsame\nsame\ntail\n",
        b"saved\nhead\nnew1\nnew2\ntail\n",
        ownership,
    )

    assert result == b"head\nnew1\nsame\ntail\n"


def test_build_realized_content_duplicate_line_claimed():
    """Test claiming one instance of a duplicated line."""
    # Base has two "X" lines
    base_content = b"A\nX\nB\nX\nC\n"

    # Batch source adds another "X" at the start
    batch_source_content = b"X\nA\nX\nB\nX\nC\n"

    # Claim line 1 (the added X)
    ownership = BatchOwnership.from_presence_lines(["1"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    result_lines = result.decode().split('\n')

    print(f"Result lines: {result_lines}")

    # Should have: X (claimed), A (base), X (base), B (base), X (base), C (base)
    # Total of 3 X's
    x_count = result_lines.count("X")
    assert x_count == 3, f"Expected 'X' to appear 3 times, but got {x_count} times: {result_lines}"


def test_build_realized_content_simple_insert():
    """Baseline test: simple insert of new line."""
    base_content = b"A\nB\n"
    batch_source_content = b"A\nNEW\nB\n"

    # Claim line 2 (NEW)
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    assert result == b"A\nNEW\nB\n"


def test_build_realized_content_uses_baseline_reference_for_ambiguous_insert():
    """Stored content honors a verified insertion boundary when context repeats."""
    base_content = b"top\n\nbottom\n"
    batch_source_content = (
        b"top\n"
        b"import-a\n"
        b"import-b\n"
        b"import-c\n"
        b"\n"
        b"save-a\n"
        b"save-b\n"
        b"later-a\n"
        b"\n"
        b"bottom\n"
    )
    ownership = BatchOwnership.from_presence_lines(
        ["6-7"],
        baseline_references={
            line: BaselineReference(
                after_line=1,
                after_content=b"top",
                before_line=2,
                before_content=b"",
                has_before_line=True,
            )
            for line in (6, 7)
        },
    )

    result = _build_realized_content_from_bytes(
        base_content,
        batch_source_content,
        ownership,
    )

    assert result == b"top\nsave-a\nsave-b\n\nbottom\n"


def test_exact_baseline_inserts_content_equal_to_following_line():
    """A baseline sibling must not masquerade as the selected insertion."""
    base_content = b"same\nb\nsame\nsame\nb\nx\n"
    batch_source_content = b"same\nsame\nsame\nsame\nb\nx\n"
    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        baseline_references={
            2: BaselineReference(
                after_line=2,
                after_content=b"b",
                before_line=3,
                before_content=b"same",
                has_before_line=True,
            ),
        },
    )

    result = _build_realized_content_from_bytes(
        base_content,
        batch_source_content,
        ownership,
    )

    assert result == b"same\nb\nsame\nsame\nsame\nb\nx\n"


def test_build_realized_content_applies_deletion_when_source_matches_baseline():
    """Baseline fallback must not bypass storage deletion constraints."""
    content = b"first\nold value\n"
    ownership = BatchOwnership(
        [],
        [
            AbsenceClaim(
                anchor_line=None,
                content_lines=content.splitlines(keepends=True),
                baseline_reference=BaselineReference(after_line=None),
            )
        ],
    )

    result = _build_realized_content_from_bytes(content, content, ownership)

    assert result == b""


def test_build_realized_content_uses_deletion_anchor_for_moved_block():
    """Stored content uses a recorded boundary when an anchor line repeats."""
    base_content = b"""def realize():
    fallback = try_baseline(
        source,
        base,
        ownership,
        presence,
        deletions,
    )
    if fallback is not None:
        return fallback

    result = satisfy_constraints(
        source,
        base,
        presence,
        deletions,
        strict=False,
    )

    return result
"""
    batch_source_content = b"""def realize():
    try:
        result = satisfy_constraints(
            source,
            base,
            presence,
            deletions,
            strict=False,
        )
    except MergeError:
        fallback = try_baseline(
            source,
            base,
            ownership,
            presence,
            deletions,
        )
        if fallback is None:
            raise
        return fallback

    return result
"""
    base_lines = base_content.splitlines(keepends=True)
    ownership = BatchOwnership.from_presence_lines(
        ["1-22"],
        [
            AbsenceClaim(
                anchor_line=1,
                content_lines=base_lines[1:9],
                baseline_reference=BaselineReference(after_line=1),
            ),
            AbsenceClaim(
                anchor_line=21,
                content_lines=base_lines[11:19],
                baseline_reference=BaselineReference(after_line=11),
            ),
        ],
        replacement_units=[
            ReplacementUnit(
                presence_lines=["2-19"],
                deletion_indices=[0],
            ),
        ],
    )

    result = _build_realized_content_from_bytes(
        base_content,
        batch_source_content,
        ownership,
    )

    assert result == batch_source_content


def test_build_realized_content_from_lines_accepts_non_list_sequences(line_sequence):
    """Realized content construction accepts indexed byte-line sequences."""
    base_lines = line_sequence([b"A\n", b"B\n"])
    batch_source_lines = line_sequence([b"A\n", b"NEW\n", b"B\n"])
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    with build_realized_buffer_from_lines(
        base_lines,
        batch_source_lines,
        ownership,
    ) as buffer:
        result = buffer.to_bytes()

    assert result == b"A\nNEW\nB\n"


def test_build_realized_buffer_from_lines_returns_buffer():
    """Realized content can be rendered into a buffer."""
    base_content = b"A\r\nB\r\n"
    batch_source_content = b"A\r\nNEW\r\nB\r\n"
    ownership = BatchOwnership.from_presence_lines(["2"], [])

    with build_realized_buffer_from_lines(
        base_content.splitlines(keepends=True),
        batch_source_content.splitlines(keepends=True),
        ownership,
    ) as result:
        assert result.to_bytes() == b"A\r\nNEW\r\nB\r\n"
        assert result.byte_count == len(b"A\r\nNEW\r\nB\r\n")


def test_build_realized_buffer_can_prefer_base_line_endings():
    """A target merge can retain LF when its batch source uses CRLF."""
    base_content = b"A\nB\n"
    batch_source_content = b"A\r\nNEW\r\nB\r\n"
    ownership = BatchOwnership.from_presence_lines(["2"], [])
    base_lines = base_content.splitlines(keepends=True)

    with build_realized_buffer_from_lines(
        base_lines,
        batch_source_content.splitlines(keepends=True),
        ownership,
        preferred_line_ending_lines=base_lines,
    ) as result:
        assert result.to_bytes() == b"A\nNEW\nB\n"


def test_build_realized_content_equal_block_with_unclaimed_insert():
    """Test that unclaimed inserts don't appear, and equal blocks work correctly."""
    # Base: A, B, C
    base_content = b"A\nB\nC\n"

    # Source: A, INSERTED, B, C (inserted between A and B)
    batch_source_content = b"A\nINSERTED\nB\nC\n"

    # Claim nothing (just see equal blocks)
    ownership = BatchOwnership.from_presence_lines([], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)

    # Should get base back unchanged since we didn't claim the insert
    assert result == b"A\nB\nC\n", f"Expected base unchanged, got: {result}"


def test_build_realized_content_equal_then_claimed_insert():
    """Test equal block followed by claimed insert."""
    # Base: A, B
    base_content = b"A\nB\n"

    # Source: A, B, NEW
    batch_source_content = b"A\nB\nNEW\n"

    # Claim line 3 (NEW)
    ownership = BatchOwnership.from_presence_lines(["3"], [])

    result = _build_realized_content_from_bytes(base_content, batch_source_content, ownership)
    lines = result.split(b'\n')

    # Should have A, B (from equal block), NEW (from insert, claimed)
    # B should not be duplicated.
    assert lines.count(b"B") == 1, f"B should appear once, got: {lines}"
    assert result == b"A\nB\nNEW\n", f"Expected A, B, NEW, got: {result}"
