"""Tests for ownership unit grouping based on replacement metadata and display.

These tests verify that explicit replacement metadata is honored first, and
remaining replacement units are formed based on adjacency in reconstructed
display order, not source-line proximity.
"""

from __future__ import annotations

import pytest

from git_stage_batch.batch.ownership.display_lines import (
    build_display_lines_from_batch_source_lines,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import (
    BatchOwnership,
)
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    LegacyReplacementUnitOrigin,
    NoReplacementUnitOrigin,
    ProvenReplacementUnitOrigin,
    ReplacementUnit,
    ReplacementUnitOrigin,
    normalize_replacement_units,
)
from git_stage_batch.batch.ownership.units import (
    build_ownership_units_from_batch_source_lines,
)
from git_stage_batch.batch.ownership.unit_rebuild import (
    rebuild_ownership_from_units,
)
from git_stage_batch.batch.ownership.unit_selection import (
    select_ownership_units_by_display_ids,
)
from git_stage_batch.batch.ownership.unit_types import (
    OwnershipUnit,
    OwnershipUnitKind,
)
from git_stage_batch.batch.selection import acquire_batch_ownership_for_display_ids_from_lines
from git_stage_batch.core.line_selection import LineRanges
from git_stage_batch.exceptions import AtomicUnitError


def test_replacement_origin_variants_distinguish_legacy_and_current_units():
    """Absent compatibility provenance is not conflated with current evidence."""
    current = ReplacementUnit(["1"], [0])
    legacy = ReplacementUnit.from_dict(
        {"presence_lines": ["1"], "deletion_indices": [0]}
    )

    assert isinstance(current.origin_evidence, NoReplacementUnitOrigin)
    assert isinstance(legacy.origin_evidence, LegacyReplacementUnitOrigin)


def test_current_replacement_origin_conflict_fails_closed():
    """Coalescing cannot silently erase disagreeing current provenance."""
    left = ReplacementUnitOrigin(1, 1, 1, 1)
    right = ReplacementUnitOrigin(2, 2, 1, 1)
    units = [
        ReplacementUnit(["1"], [0], origin=left),
        ReplacementUnit(["1"], [0], origin=right),
    ]

    assert isinstance(units[0].origin_evidence, ProvenReplacementUnitOrigin)
    with pytest.raises(ValueError, match="disagree"):
        normalize_replacement_units(units, deletion_count=1)


def test_current_replacement_origin_cannot_be_silently_downgraded_to_legacy():
    """Only the compatibility decoder may construct legacy provenance."""
    with pytest.raises(ValueError, match="provenance is malformed"):
        ReplacementUnit(
            ["1"],
            [0],
            origin={},  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "evidence_type",
    [ProvenReplacementUnitOrigin, LegacyReplacementUnitOrigin],
)
def test_rebuild_preserves_replacement_origin_authority_tier(evidence_type):
    """Semantic-unit round trips cannot promote or demote provenance."""
    origin = ReplacementUnitOrigin(1, 1, 1, 1)
    unit = OwnershipUnit(
        kind=OwnershipUnitKind.REPLACEMENT,
        claimed_source_lines=LineRanges.from_ranges(((1, 1),)),
        deletion_claims=[
            AbsenceClaim(anchor_line=None, content_lines=(b"old\n",))
        ],
        display_line_ids=LineRanges.from_ranges(((1, 2),)),
        preserves_replacement_unit=True,
        replacement_origin_evidence=evidence_type(origin),
    )

    rebuilt = rebuild_ownership_from_units([unit])

    assert isinstance(
        rebuilt.replacement_units[0].origin_evidence,
        evidence_type,
    )


def test_replacement_normalization_coalesces_transitive_overlap() -> None:
    """Presence and deletion overlap form one transitive replacement unit."""
    units = [
        ReplacementUnit(["1-2"], [0]),
        ReplacementUnit(["4"], [1]),
        ReplacementUnit(["2-4"], [2]),
        ReplacementUnit(["8"], [2]),
    ]

    assert normalize_replacement_units(units, deletion_count=3) == [
        ReplacementUnit(["1-4,8"], [0, 1, 2]),
    ]


def test_replacement_normalization_does_not_scan_all_disjoint_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disjoint units use indexes instead of an all-components overlap scan."""
    intersection_calls = 0
    original_intersection = LineRanges.intersection

    def counted_intersection(
        ranges: LineRanges,
        other: LineRanges,
    ) -> LineRanges:
        nonlocal intersection_calls
        intersection_calls += 1
        return original_intersection(ranges, other)

    monkeypatch.setattr(LineRanges, "intersection", counted_intersection)
    unit_count = 512
    units = [
        ReplacementUnit([str(2 * index + 1)], [index])
        for index in range(unit_count)
    ]

    normalized = normalize_replacement_units(
        units,
        deletion_count=unit_count,
    )

    assert normalized == units
    assert intersection_calls <= unit_count


def test_explicit_unit_display_projection_does_not_rescan_for_every_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many disjoint units are joined to display rows with one indexed pass."""
    contains_calls = 0
    original_contains = LineRanges.__contains__

    def counted_contains(ranges: LineRanges, line_number: object) -> bool:
        nonlocal contains_calls
        contains_calls += 1
        return original_contains(ranges, line_number)

    monkeypatch.setattr(LineRanges, "__contains__", counted_contains)
    unit_count = 256
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{unit_count}"],
        [
            AbsenceClaim(anchor_line=line_number, content_lines=[b"old\n"])
            for line_number in range(1, unit_count + 1)
        ],
        replacement_units=[
            ReplacementUnit([str(line_number)], [line_number - 1])
            for line_number in range(1, unit_count + 1)
        ],
    )

    units = _ownership_units_for_source(
        ownership,
        b"new\n" * unit_count,
    )

    assert len(units) == unit_count
    assert contains_calls <= unit_count * 5


def test_rebuild_collects_fragmented_presence_without_iterative_unions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rebuild normalization is one bulk operation, not quadratic unions."""
    def unexpected_union(*_args: object, **_kwargs: object) -> LineRanges:
        raise AssertionError("ownership rebuild used iterative range unions")

    monkeypatch.setattr(LineRanges, "union", unexpected_union)
    unit_count = 512
    units = [
        OwnershipUnit(
            kind=OwnershipUnitKind.PRESENCE_ONLY,
            claimed_source_lines=LineRanges.from_ranges(((2 * index + 1,) * 2,)),
            deletion_claims=[],
            display_line_ids=LineRanges.from_ranges(((index + 1,) * 2,)),
        )
        for index in range(unit_count)
    ]

    rebuilt = rebuild_ownership_from_units(units)

    assert len(rebuilt.presence_line_set()) == unit_count


def test_presence_reference_index_is_built_once_for_many_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-line ownership units do not rebuild the complete reference map."""
    line_count = 512
    references = {
        line_number: BaselineReference(after_line=line_number)
        for line_number in range(1, line_count + 1)
    }
    ownership = BatchOwnership.from_presence_lines(
        [f"1-{line_count}"],
        baseline_references=references,
    )
    calls = 0
    original_references = ownership.presence_baseline_references

    def counted_references() -> dict[int, BaselineReference]:
        nonlocal calls
        calls += 1
        return original_references()

    monkeypatch.setattr(
        ownership,
        "presence_baseline_references",
        counted_references,
    )

    units = _ownership_units_for_source(
        ownership,
        b"owned\n" * line_count,
    )

    assert len(units) == line_count
    assert calls == 1


def test_legacy_malformed_origin_is_repaired_without_becoming_provenance():
    """Compatibility decoding may drop bad evidence but tags the result legacy."""
    unit = ReplacementUnit.from_dict(
        {
            "presence_lines": ["1"],
            "deletion_indices": [0],
            "original_unit": {
                "old_start": 0,
                "old_end": 0,
                "new_start": 0,
                "new_end": 0,
            },
        }
    )

    assert isinstance(unit.origin_evidence, LegacyReplacementUnitOrigin)
    assert unit.origin is None


def _source_lines(content: bytes) -> list[bytes]:
    return content.splitlines(keepends=True)


def _source_lines_from_text(content: str) -> list[bytes]:
    return [
        line.encode("utf-8")
        for line in content.splitlines(keepends=True)
    ]


def _ownership_units_for_source(
    ownership: BatchOwnership,
    source: bytes,
) -> list[OwnershipUnit]:
    return build_ownership_units_from_batch_source_lines(
        ownership,
        _source_lines(source),
    )


def test_display_includes_context_between_separated_claimed_lines():
    """Separated owned lines should not be visually glued together."""
    batch_source = "def func(\n    arg1,\n    arg2,\n):\n    return arg1\n"
    ownership = BatchOwnership.from_presence_lines(["1,5"], [])

    display_lines = build_display_lines_from_batch_source_lines(
        _source_lines_from_text(batch_source),
        ownership,
        context_lines=10,
    )

    assert [line["type"] for line in display_lines] == [
        "claimed",
        "context",
        "context",
        "context",
        "claimed",
    ]
    assert display_lines[0]["id"] == 1
    assert display_lines[1]["id"] is None
    assert display_lines[1]["content"] == "    arg1,\n"
    assert display_lines[3]["content"] == "):\n"
    assert display_lines[4]["id"] == 2


def test_display_context_honors_context_lines_limit():
    """Unowned source context should be bounded by the requested context width."""
    batch_source = "".join(f"line {i}\n" for i in range(1, 11))
    ownership = BatchOwnership.from_presence_lines(["2,9"], [])

    display_lines = build_display_lines_from_batch_source_lines(
        _source_lines_from_text(batch_source),
        ownership,
        context_lines=1,
    )

    assert [line["content"] for line in display_lines] == [
        "line 1\n",
        "line 2\n",
        "line 3\n",
        "... 4 more lines ...\n",
        "line 8\n",
        "line 9\n",
        "line 10\n",
    ]
    assert [line["type"] for line in display_lines] == [
        "context",
        "claimed",
        "context",
        "gap",
        "context",
        "claimed",
        "context",
    ]
    assert [line["id"] for line in display_lines] == [None, 1, None, None, None, 2, None]


def test_display_context_zero_omits_unowned_context():
    """-U0 style display should show only owned lines and deletion constraints."""
    batch_source = "line 1\nline 2\nline 3\n"
    ownership = BatchOwnership.from_presence_lines(["1,3"], [])

    display_lines = build_display_lines_from_batch_source_lines(
        _source_lines_from_text(batch_source),
        ownership,
        context_lines=0,
    )

    assert [line["content"] for line in display_lines] == [
        "line 1\n",
        "... 1 more line ...\n",
        "line 3\n",
    ]
    assert [line["id"] for line in display_lines] == [1, None, 2]


def test_deletion_followed_by_claimed_becomes_replacement():
    """Test deletion block immediately followed by claimed block forms REPLACEMENT unit."""
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    #
    # Ownership:
    # - Delete lines 1-2
    # - Add new line at position 1
    #
    # Display will show:
    # [deletion] old line 1
    # [deletion] old line 2
    # [claimed]  new line 1
    # [context]  old line 3

    batch_source = b"old line 1\nold line 2\nold line 3\n"

    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old line 1\n", b"old line 2\n"])
        ]
    )

    units = _ownership_units_for_source(ownership, batch_source)

    # Should have exactly one REPLACEMENT unit
    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].is_atomic is True
    assert len(units[0].deletion_claims) == 1
    assert units[0].claimed_source_lines == {1}


def test_build_ownership_units_accepts_batch_source_line_sequence(line_sequence):
    """Ownership unit grouping accepts indexed batch-source lines."""
    source_lines = line_sequence([
        b"old line 1\n",
        b"old line 2\n",
        b"old line 3\n",
    ])
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old line 1\n"])
        ],
    )

    units = build_ownership_units_from_batch_source_lines(ownership, source_lines)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].is_atomic is True
    assert units[0].claimed_source_lines == {1}


def test_select_batch_ownership_accepts_batch_source_line_sequence(line_sequence):
    """Line selection can reconstruct ownership from indexed batch-source lines."""
    source_lines = line_sequence([
        b"line 1\n",
        b"line 2\n",
        b"line 3\n",
    ])
    ownership = BatchOwnership.from_presence_lines(["1,3"], [])
    file_meta = ownership.to_metadata_dict()

    with acquire_batch_ownership_for_display_ids_from_lines(
        file_meta,
        source_lines,
        {2},
    ) as selected:
        assert selected.presence_line_set() == {3}


def test_selected_legacy_replacement_carries_contiguous_continuation():
    """Merge selection restores the full legacy replacement presence run."""
    source_lines = _source_lines(b"head\nnew first\nnew second\ntail\n")
    deletion = AbsenceClaim(
        anchor_line=1,
        content_lines=[b"old call\n"],
    )
    ownership = BatchOwnership.from_presence_lines(
        ["2-3"],
        [deletion],
    )
    file_meta = ownership.to_metadata_dict()

    with acquire_batch_ownership_for_display_ids_from_lines(
        file_meta,
        source_lines,
        {1, 2},
    ) as selected:
        assert selected.presence_line_set() == {2, 3}
        assert len(selected.deletions) == 1
        assert selected.deletions[0].anchor_line == 1
        assert list(selected.deletions[0].content_lines) == [b"old call\n"]
        assert selected.replacement_units == [
            ReplacementUnit(
                presence_lines=["2-3"],
                deletion_indices=[0],
            )
        ]


def test_selected_legacy_replacement_completion_does_not_union_per_line(
    monkeypatch,
):
    """Long legacy continuations are accumulated as one compact range."""
    union_calls = 0
    original_union = LineRanges.union

    def count_union(left, right):
        nonlocal union_calls
        union_calls += 1
        return original_union(left, right)

    monkeypatch.setattr(LineRanges, "union", count_union)
    def completion_union_count(continuation_count):
        nonlocal union_calls
        source_lines = _source_lines(
            b"head\n"
            + b"".join(
                f"new {line}\n".encode()
                for line in range(continuation_count)
            )
            + b"tail\n"
        )
        ownership = BatchOwnership.from_presence_lines(
            [f"2-{continuation_count + 1}"],
            [AbsenceClaim(anchor_line=1, content_lines=[b"old call\n"])],
        )
        calls_before = union_calls
        with acquire_batch_ownership_for_display_ids_from_lines(
            ownership.to_metadata_dict(),
            source_lines,
            {1, 2},
        ) as selected:
            assert selected.presence_line_set().ranges() == (
                (2, continuation_count + 1),
            )
        return union_calls - calls_before

    assert completion_union_count(128) == completion_union_count(8)



def test_claimed_followed_by_deletion_becomes_replacement():
    """Test claimed line immediately followed by deletion block forms REPLACEMENT unit."""
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    #
    # Ownership:
    # - Add new line at position 1
    # - Delete line 2
    #
    # Display will show:
    # [claimed]  new line 1
    # [deletion] old line 2
    # [context]  old line 1
    # [context]  old line 3

    batch_source = b"old line 1\nold line 2\nold line 3\n"

    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=1, content_lines=[b"old line 2\n"])
        ]
    )

    units = _ownership_units_for_source(ownership, batch_source)

    # Should have exactly one REPLACEMENT unit
    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].is_atomic is True


def test_deletion_without_adjacent_claimed_is_deletion_only():
    """Test deletion block with no adjacent claimed block forms DELETION_ONLY unit."""
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    #
    # Ownership:
    # - Delete line 2 only
    #
    # Display will show:
    # [context]  old line 1
    # [deletion] old line 2
    # [context]  old line 3

    batch_source = b"old line 1\nold line 2\nold line 3\n"

    ownership = BatchOwnership.from_presence_lines(
        [],
        [
            AbsenceClaim(anchor_line=1, content_lines=[b"old line 2\n"])
        ]
    )

    units = _ownership_units_for_source(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.DELETION_ONLY
    assert units[0].is_atomic is True
    assert len(units[0].deletion_claims) == 1
    assert units[0].claimed_source_lines == set()


def test_claimed_without_adjacent_deletion_is_presence_only():
    """Test claimed line with no adjacent deletion block forms PRESENCE_ONLY unit."""
    # Source content:
    # 1: old line 1
    # 2: old line 2
    #
    # Ownership:
    # - Add new line at position 2 (between existing lines)
    #
    # Display will show:
    # [context]  old line 1
    # [claimed]  new line 2
    # [context]  old line 2

    batch_source = b"old line 1\nold line 2\n"

    ownership = BatchOwnership.from_presence_lines(["2"], [])

    units = _ownership_units_for_source(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.PRESENCE_ONLY
    assert units[0].is_atomic is False
    assert type(units[0].claimed_source_lines) is LineRanges
    assert type(units[0].display_line_ids) is LineRanges
    assert units[0].claimed_source_lines == {2}
    assert units[0].deletion_claims == []


def test_nearby_in_source_separated_in_display_not_coupled():
    """Test that source-line proximity does not cause coupling if display separates them.

    A deletion and claimed line can be numerically close in source space but
    separated in display by other owned content. They must remain independent
    units based on display structure, not source proximity.
    """
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    # 4: old line 4
    # 5: old line 5
    #
    # Ownership:
    # - Delete line 2
    # - Add new line at position 2 (replacement candidate, source-space close)
    # - Add another line at position 4 (separates them in display)
    #
    # Display shows (in order):
    # [deletion] old line 2        <- deletion anchored at line 1
    # [claimed]  line 2             <- would be adjacent to deletion
    # [claimed]  line 4             <- BUT this separates them
    #
    # Wait, this doesn't work either because the deletion comes first,
    # then all claimed lines in source order.
    #
    # Let me use a different approach: deletion anchored later, claimed earlier:
    # - Add new line at position 2
    # - Delete line 4 (anchored at line 3)
    #
    # Display shows:
    # [claimed]  new line at 2
    # [deletion] old line 4
    #
    # These ARE adjacent in display, so they WILL couple.
    #
    # To truly separate them, I need intermediate content. Since display only
    # shows owned content, I need:
    # - claimed line
    # - ANOTHER claimed/deletion
    # - deletion
    #
    # Example:
    # - Delete line 1
    # - Add line 2
    # - Delete line 4
    #
    # Display shows:
    # [deletion] old line 1
    # [claimed]  new line 2
    # [deletion] old line 4
    #
    # The two deletions are separated by the claimed line, so they should be
    # separate units: (deletion1 + claimed) as REPLACEMENT, deletion2 as DELETION_ONLY

    batch_source = b"old line 1\nold line 2\nold line 3\nold line 4\nold line 5\n"

    ownership = BatchOwnership.from_presence_lines(
        ["2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old line 1\n"]),
            AbsenceClaim(anchor_line=3, content_lines=[b"old line 4\n"])
        ]
    )

    units = _ownership_units_for_source(ownership, batch_source)

    # Should have TWO units:
    # - REPLACEMENT (deletion of line 1 + claimed line 2)
    # - DELETION_ONLY (deletion of line 4)
    assert len(units) == 2

    replacement_units = [u for u in units if u.kind == OwnershipUnitKind.REPLACEMENT]
    deletion_units = [u for u in units if u.kind == OwnershipUnitKind.DELETION_ONLY]

    assert len(replacement_units) == 1
    assert len(deletion_units) == 1

    # Replacement unit: deletion1 + claimed line 2
    replacement = replacement_units[0]
    assert replacement.is_atomic is True
    assert replacement.claimed_source_lines == {2}
    assert len(replacement.deletion_claims) == 1
    # The deletion should be the one anchored at None (line 1)
    assert replacement.deletion_claims[0].anchor_line is None

    # Deletion unit: just deletion of line 4
    deletion = deletion_units[0]
    assert deletion.is_atomic is True
    assert deletion.claimed_source_lines == set()
    assert len(deletion.deletion_claims) == 1
    # The deletion should be the one anchored at line 3 (deleting line 4)
    assert deletion.deletion_claims[0].anchor_line == 3


def test_multiple_presence_only_lines_remain_independently_selectable():
    """Test that multiple non-adjacent claimed lines form separate PRESENCE_ONLY units.

    This ensures we don't accidentally over-group unrelated presence claims.
    """
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    # 4: old line 4
    #
    # Ownership:
    # - Add new line at position 1
    # - Add new line at position 3
    # (positions separated by context in display)
    #
    # Display shows:
    # [claimed]  new line 1
    # [context]  old line 1
    # [context]  old line 2
    # [claimed]  new line 3
    # [context]  old line 3
    # [context]  old line 4

    batch_source = b"old line 1\nold line 2\nold line 3\nold line 4\n"

    ownership = BatchOwnership.from_presence_lines(["1", "3"], [])

    units = _ownership_units_for_source(ownership, batch_source)

    # Should have TWO separate PRESENCE_ONLY units
    assert len(units) == 2
    assert all(u.kind == OwnershipUnitKind.PRESENCE_ONLY for u in units)
    assert all(u.is_atomic is False for u in units)

    # Each should have exactly one claimed line
    claimed_lines = {frozenset(u.claimed_source_lines) for u in units}
    assert claimed_lines == {frozenset({1}), frozenset({3})}


def test_multiple_consecutive_deletions_and_claims_form_single_replacement():
    """Test legacy display-adjacency fallback for replacement grouping.

    Without persisted replacement metadata, a deletion block couples with the
    first claimed line and the remaining claimed lines stay independent.
    """
    # Source content:
    # 1: old line 1
    # 2: old line 2
    # 3: old line 3
    # 4: old line 4
    #
    # Ownership:
    # - Delete lines 1-2
    # - Add new lines at positions 1-2
    #
    # Display shows:
    # [deletion] old line 1
    # [deletion] old line 2
    # [claimed]  new line 1
    # [claimed]  new line 2
    # [context]  old line 3
    # [context]  old line 4

    batch_source = b"old line 1\nold line 2\nold line 3\nold line 4\n"

    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old line 1\n", b"old line 2\n"])
        ]
    )

    units = _ownership_units_for_source(ownership, batch_source)

    # Should be two units:
    # - REPLACEMENT containing deletions + first claimed line
    # - PRESENCE_ONLY for second claimed line (allows independent reset)
    assert len(units) == 2

    replacement_units = [u for u in units if u.kind == OwnershipUnitKind.REPLACEMENT]
    presence_units = [u for u in units if u.kind == OwnershipUnitKind.PRESENCE_ONLY]

    assert len(replacement_units) == 1
    assert len(presence_units) == 1

    # REPLACEMENT couples deletion block with first claimed line only
    assert replacement_units[0].claimed_source_lines == {1}
    assert len(replacement_units[0].deletion_claims) == 1
    assert replacement_units[0].is_atomic is True

    # Second claimed line is PRESENCE_ONLY (independently selectable)
    assert presence_units[0].claimed_source_lines == {2}
    assert presence_units[0].is_atomic is False


def test_rebuild_does_not_promote_display_adjacency_to_explicit_metadata():
    """Inferred display adjacency should not become persisted replacement intent."""
    batch_source = b"new line\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old line\n"])
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        _ownership_units_for_source(ownership, batch_source)
    )

    assert rebuilt.presence_claims[0].source_lines == ["1"]
    assert len(rebuilt.deletions) == 1
    assert rebuilt.replacement_units == []


def test_explicit_replacement_unit_overrides_display_adjacency():
    """Persisted replacement metadata should couple non-adjacent display lines."""
    batch_source = b"new first\ncontext\nanchor\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            AbsenceClaim(anchor_line=3, content_lines=[b"old later\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    units = _ownership_units_for_source(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].preserves_replacement_unit is True
    assert units[0].claimed_source_lines == {1}
    assert units[0].deletion_claims == ownership.deletions


def test_explicit_replacement_unit_can_group_multiple_claimed_lines():
    """Explicit metadata can preserve a whole multi-line replacement unit."""
    batch_source = b"new one\nnew two\nkeep\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
        ],
    )

    units = _ownership_units_for_source(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert type(units[0].claimed_source_lines) is LineRanges
    assert type(units[0].display_line_ids) is LineRanges
    assert units[0].claimed_source_lines == {1, 2}
    assert units[0].display_line_ids == {1, 2, 3, 4}


def test_partial_atomic_selection_reports_range_backed_display_ids():
    """Atomic selection errors should format IDs without a set-only contract."""
    batch_source = b"new one\nnew two\nkeep\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
        ],
    )

    units = _ownership_units_for_source(ownership, batch_source)

    with pytest.raises(AtomicUnitError) as error:
        select_ownership_units_by_display_ids(
            units,
            LineRanges.from_ranges([(1, 1)]),
        )

    assert "Select all related lines together: 1-4" in str(error.value)
    assert "You selected: 1" in str(error.value)
    assert type(error.value.required_selection_ids) is LineRanges


def test_rebuild_preserves_explicit_replacement_units():
    """Filtering/rebuilding ownership should persist replacement couplings."""
    batch_source = b"new one\nnew two\nkeep\n"
    origin = ReplacementUnitOrigin(
        old_start=1,
        old_end=2,
        new_start=1,
        new_end=2,
        baseline_reference=BaselineReference(after_line=None),
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            AbsenceClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(
                presence_lines=["1-2"],
                deletion_indices=[0],
                origin=origin,
            ),
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        _ownership_units_for_source(ownership, batch_source)
    )

    assert rebuilt.presence_claims[0].source_lines == ["1-2"]
    assert len(rebuilt.deletions) == 1
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]
    assert rebuilt.replacement_units[0].origin == origin


def test_rebuild_preserves_presence_baseline_references():
    """Unit filtering must retain placement identity for surviving claims."""
    first_reference = BaselineReference(
        after_line=1,
        after_content=b"anchor one\n",
    )
    second_reference = BaselineReference(
        after_line=2,
        after_content=b"anchor two\n",
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        baseline_references={1: first_reference, 2: second_reference},
    )
    units = _ownership_units_for_source(ownership, b"first\nsecond\n")

    rebuilt = rebuild_ownership_from_units([units[1]])

    assert rebuilt.presence_claims[0].source_lines == ["2"]
    assert rebuilt.presence_claims[0].baseline_references == {
        2: second_reference,
    }


def test_rebuild_preserves_mixed_same_anchor_deletion_order():
    """Same-anchor explicit and inferred deletions should keep stable indexes."""
    batch_source = b"new explicit\nnew inferred\nkeep\n"
    explicit_deletion = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old explicit\n"],
    )
    inferred_deletion = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old inferred\n"],
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            explicit_deletion,
            inferred_deletion,
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        _ownership_units_for_source(ownership, batch_source)
    )

    assert rebuilt.deletions == [explicit_deletion, inferred_deletion]
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]


def test_rebuild_preserves_mixed_same_anchor_order_when_explicit_is_later():
    """Later explicit replacements should not reorder earlier inferred deletions."""
    batch_source = b"new explicit\nkeep\n"
    inferred_deletion = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old inferred\n"],
    )
    explicit_deletion = AbsenceClaim(
        anchor_line=None,
        content_lines=[b"old explicit\n"],
    )
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            inferred_deletion,
            explicit_deletion,
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[1]),
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        _ownership_units_for_source(ownership, batch_source)
    )

    assert rebuilt.deletions == [inferred_deletion, explicit_deletion]
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[1]),
    ]
