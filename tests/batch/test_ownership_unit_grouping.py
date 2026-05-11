"""Tests for ownership unit grouping based on replacement metadata and display.

These tests verify that explicit replacement metadata is honored first, and
remaining replacement units are formed based on adjacency in reconstructed
display order, not source-line proximity.
"""

from __future__ import annotations


from git_stage_batch.batch.ownership import (
    BatchOwnership,
    DeletionClaim,
    build_ownership_units_from_display,
    build_ownership_units_from_batch_source_lines,
    OwnershipUnitKind,
    ReplacementUnit,
    rebuild_ownership_from_units,
)
from git_stage_batch.batch.display import build_display_lines_from_batch_source
from git_stage_batch.batch.selection import select_batch_ownership_for_display_ids_from_lines


def test_display_includes_context_between_separated_claimed_lines():
    """Separated owned lines should not be visually glued together."""
    batch_source = "def func(\n    arg1,\n    arg2,\n):\n    return arg1\n"
    ownership = BatchOwnership.from_presence_lines(["1,5"], [])

    display_lines = build_display_lines_from_batch_source(batch_source, ownership, context_lines=10)

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

    display_lines = build_display_lines_from_batch_source(batch_source, ownership, context_lines=1)

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

    display_lines = build_display_lines_from_batch_source(batch_source, ownership, context_lines=0)

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
            DeletionClaim(anchor_line=None, content_lines=[b"old line 1\n", b"old line 2\n"])
        ]
    )

    units = build_ownership_units_from_display(ownership, batch_source)

    # Should have exactly one REPLACEMENT unit
    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].is_atomic is True
    assert units[0].atomic_reason == "display_adjacency"
    assert len(units[0].deletion_claims) == 1
    assert units[0].claimed_source_lines == {1}


def test_build_ownership_units_accepts_batch_source_line_sequence(line_sequence):
    """Ownership unit grouping accepts indexed batch-source byte lines."""
    source_lines = line_sequence([
        b"old line 1\n",
        b"old line 2\n",
        b"old line 3\n",
    ])
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old line 1\n"])
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

    selected = select_batch_ownership_for_display_ids_from_lines(
        file_meta,
        source_lines,
        {2},
    )

    assert selected.presence_line_set() == {3}


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
            DeletionClaim(anchor_line=1, content_lines=[b"old line 2\n"])
        ]
    )

    units = build_ownership_units_from_display(ownership, batch_source)

    # Should have exactly one REPLACEMENT unit
    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].is_atomic is True
    assert units[0].atomic_reason == "display_adjacency"


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
            DeletionClaim(anchor_line=1, content_lines=[b"old line 2\n"])
        ]
    )

    units = build_ownership_units_from_display(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.DELETION_ONLY
    assert units[0].is_atomic is True
    assert units[0].atomic_reason == "deletion_only"
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

    units = build_ownership_units_from_display(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.PRESENCE_ONLY
    assert units[0].is_atomic is False
    assert units[0].atomic_reason is None
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
            DeletionClaim(anchor_line=None, content_lines=[b"old line 1\n"]),
            DeletionClaim(anchor_line=3, content_lines=[b"old line 4\n"])
        ]
    )

    units = build_ownership_units_from_display(ownership, batch_source)

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

    units = build_ownership_units_from_display(ownership, batch_source)

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
            DeletionClaim(anchor_line=None, content_lines=[b"old line 1\n", b"old line 2\n"])
        ]
    )

    units = build_ownership_units_from_display(ownership, batch_source)

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
            DeletionClaim(anchor_line=None, content_lines=[b"old line\n"])
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        build_ownership_units_from_display(ownership, batch_source)
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
            DeletionClaim(anchor_line=3, content_lines=[b"old later\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
        ],
    )

    units = build_ownership_units_from_display(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].atomic_reason == "explicit_replacement"
    assert units[0].preserves_replacement_unit is True
    assert units[0].claimed_source_lines == {1}
    assert units[0].deletion_claims == ownership.deletions


def test_explicit_replacement_unit_can_group_multiple_claimed_lines():
    """Explicit metadata can preserve a whole multi-line replacement unit."""
    batch_source = b"new one\nnew two\nkeep\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
        ],
    )

    units = build_ownership_units_from_display(ownership, batch_source)

    assert len(units) == 1
    assert units[0].kind == OwnershipUnitKind.REPLACEMENT
    assert units[0].claimed_source_lines == {1, 2}
    assert units[0].display_line_ids == {1, 2, 3, 4}


def test_rebuild_preserves_explicit_replacement_units():
    """Filtering/rebuilding ownership should persist replacement couplings."""
    batch_source = b"new one\nnew two\nkeep\n"
    ownership = BatchOwnership.from_presence_lines(
        ["1-2"],
        [
            DeletionClaim(anchor_line=None, content_lines=[b"old one\n", b"old two\n"]),
        ],
        replacement_units=[
            ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
        ],
    )

    rebuilt = rebuild_ownership_from_units(
        build_ownership_units_from_display(ownership, batch_source)
    )

    assert rebuilt.presence_claims[0].source_lines == ["1-2"]
    assert len(rebuilt.deletions) == 1
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1-2"], deletion_indices=[0]),
    ]


def test_rebuild_preserves_mixed_same_anchor_deletion_order():
    """Same-anchor explicit and inferred deletions should keep stable indexes."""
    batch_source = b"new explicit\nnew inferred\nkeep\n"
    explicit_deletion = DeletionClaim(
        anchor_line=None,
        content_lines=[b"old explicit\n"],
    )
    inferred_deletion = DeletionClaim(
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
        build_ownership_units_from_display(ownership, batch_source)
    )

    assert rebuilt.deletions == [explicit_deletion, inferred_deletion]
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[0]),
    ]


def test_rebuild_preserves_mixed_same_anchor_order_when_explicit_is_later():
    """Later explicit replacements should not reorder earlier inferred deletions."""
    batch_source = b"new explicit\nkeep\n"
    inferred_deletion = DeletionClaim(
        anchor_line=None,
        content_lines=[b"old inferred\n"],
    )
    explicit_deletion = DeletionClaim(
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
        build_ownership_units_from_display(ownership, batch_source)
    )

    assert rebuilt.deletions == [inferred_deletion, explicit_deletion]
    assert rebuilt.replacement_units == [
        ReplacementUnit(presence_lines=["1"], deletion_indices=[1]),
    ]
