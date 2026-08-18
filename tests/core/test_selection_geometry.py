"""Tests for one-time semantic selection resolution."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import gc
import tracemalloc

import pytest

from git_stage_batch.core import selection_geometry
from git_stage_batch.core.coordinates import (
    DiffNewSpace,
    DiffOldSpace,
    DisplayLineId,
    FileSnapshot,
    SnapshotIdentity,
)
from git_stage_batch.core.models import HunkHeader, LineEntry, LineLevelChange
from git_stage_batch.core.selection_geometry import (
    Deletion,
    DisplayIdRanges,
    DiffViewIdentity,
    ExactContentWitness,
    FileDiff,
    Insertion,
    Replacement,
    diff_view_identity,
    resolve_selection,
)


def _view(
    changes: LineLevelChange,
    old_identity: str = "old",
    new_identity: str = "new",
):
    return diff_view_identity(
        changes,
        old_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", old_identity), 3, DiffOldSpace
        ),
        new_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", new_identity), 3, DiffNewSpace
        ),
    )


def test_resolve_selection_returns_half_open_semantic_replacement():
    """Display IDs resolve to typed old/new spans and exact bytes once."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 3, 1, 3),
        [
            LineEntry(None, " ", 1, 1, text_bytes=b"head"),
            LineEntry(1, "-", 2, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 2, text_bytes=b"new"),
            LineEntry(None, " ", 3, 3, text_bytes=b"tail"),
        ],
    )

    selection = resolve_selection(
        changes,
        (DisplayLineId(1), DisplayLineId(2)),
        view=_view(changes),
    )

    assert selection.old_spans.ranges.ranges() == ((1, 2),)
    assert selection.new_spans.ranges.ranges() == ((1, 2),)
    assert selection.change_units == (
        Replacement(
            old_span=selection.change_units[0].old_span,
            new_span=selection.change_units[0].new_span,
            old_content=ExactContentWitness.from_lines((b"old\n",)),
            new_content=ExactContentWitness.from_lines((b"new\n",)),
        ),
    )
    assert isinstance(selection.file_diff, FileDiff)
    assert selection.file_diff.view._rendered_view is None
    assert selection.display_ids.ranges() == ((1, 2),)


def test_resolve_selection_rejects_foreign_display_id():
    """A display handle from another view cannot become a coordinate."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 1, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"new")],
    )

    with pytest.raises(ValueError, match="outside"):
        resolve_selection(changes, (DisplayLineId(2),), view=_view(changes))


def test_selection_units_do_not_cross_unselected_changed_rows():
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 3, 1, 0),
        [
            LineEntry(1, "-", 1, None, text_bytes=b"first\n"),
            LineEntry(2, "-", 2, None, text_bytes=b"middle\n"),
            LineEntry(3, "-", 3, None, text_bytes=b"last\n"),
        ],
    )

    selection = resolve_selection(
        changes,
        (DisplayLineId(1), DisplayLineId(3)),
        view=_view(changes),
    )

    assert len(selection.change_units) == 2
    assert all(isinstance(unit, Deletion) for unit in selection.change_units)
    assert [len(unit.old_span) for unit in selection.change_units] == [1, 1]


def test_partial_replacement_uses_the_change_block_boundary():
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 3, 1, 3),
        [
            LineEntry(None, " ", 1, 1, text_bytes=b"head"),
            LineEntry(1, "-", 2, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 2, text_bytes=b"new"),
            LineEntry(None, " ", 3, 3, text_bytes=b"tail"),
        ],
    )
    view = _view(changes)

    insertion = resolve_selection(
        changes,
        (DisplayLineId(2),),
        view=view,
    ).change_units
    deletion = resolve_selection(
        changes,
        (DisplayLineId(1),),
        view=view,
    ).change_units

    assert insertion == (
        Insertion(
            old_boundary=insertion[0].old_boundary,
            new_span=insertion[0].new_span,
            content=ExactContentWitness.from_lines((b"new\n",)),
        ),
    )
    assert insertion[0].old_boundary.offset == 1
    assert deletion == (
        Deletion(
            old_span=deletion[0].old_span,
            new_boundary=deletion[0].new_boundary,
            content=ExactContentWitness.from_lines((b"old\n",)),
        ),
    )
    assert deletion[0].new_boundary.offset == 1


def test_resolved_selection_rejects_endpoint_path_mismatch():
    """A view cannot bind numeric rows to another repository path."""
    view = DiffViewIdentity(
        FileSnapshot(
            "other.txt",
            SnapshotIdentity("test", "old"),
            0,
            DiffOldSpace,
        ),
        FileSnapshot(
            "other.txt",
            SnapshotIdentity("test", "new"),
            0,
            DiffNewSpace,
        ),
        SnapshotIdentity("diff-view-sha256", "view"),
    )
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 0, 0),
        [LineEntry(1, "+", None, 1, text_bytes=b"new")],
    )

    with pytest.raises(ValueError, match="path"):
        resolve_selection(changes, (DisplayLineId(1),), view=view)


def test_rendered_diff_rows_are_immutable_presentation_values():
    """Domain code cannot temporarily overwrite a rendered row coordinate."""
    line = LineEntry(1, "+", None, 1, text_bytes=b"new", source_line=1)

    with pytest.raises(FrozenInstanceError):
        line.source_line = 2  # type: ignore[misc]

def test_resolve_selection_rejects_rows_from_another_rendered_view():
    """Matching numeric display IDs do not authorize stale rendered rows."""
    original = LineLevelChange(
        "file.txt",
        HunkHeader(1, 1, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"first")],
    )
    stale_view = _view(original)
    regenerated = LineLevelChange(
        "file.txt",
        HunkHeader(1, 1, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"duplicate occurrence")],
    )

    with pytest.raises(ValueError, match="view identity"):
        resolve_selection(
            regenerated,
            (DisplayLineId(1),),
            view=stale_view,
        )


def test_bound_view_is_revalidated_during_selection_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A view capability is checked again at the domain authority boundary."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"new")],
    )
    view = _view(changes)
    original = selection_geometry.diff_view_identity
    rehash_count = 0
    def counted_rehash(*args: object, **kwargs: object):
        nonlocal rehash_count
        rehash_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(selection_geometry, "diff_view_identity", counted_rehash)

    selection = resolve_selection(changes, (DisplayLineId(1),), view=view)

    assert selection.display_ids.ranges() == ((1, 1),)
    assert rehash_count == 1


def test_bound_view_rehashes_after_in_place_row_mutation() -> None:
    """Same-object identity cannot bypass validation after its rows change."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"original")],
    )
    view = _view(changes)
    changes.lines[0] = LineEntry(1, "+", None, 1, text_bytes=b"mutated")

    with pytest.raises(ValueError, match="view identity"):
        resolve_selection(
            changes,
            (DisplayLineId(1),),
            view=view,
        )


def test_bound_view_rejects_mutation_through_original_row_list_alias() -> None:
    """A retained constructor-list alias cannot bypass view validation."""
    rows = [LineEntry(1, "+", None, 1, text_bytes=b"original")]
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        rows,
    )
    view = _view(changes)
    rows[0] = LineEntry(1, "+", None, 1, text_bytes=b"mutated")

    with pytest.raises(ValueError, match="view identity"):
        resolve_selection(
            changes,
            (DisplayLineId(1),),
            view=view,
        )


def test_display_id_witness_normalizes_order_and_duplicates() -> None:
    """The compact witness preserves the prior set-like input behavior."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 1, 1, 1),
        [
            LineEntry(1, "-", 1, None, text_bytes=b"old"),
            LineEntry(2, "+", None, 1, text_bytes=b"new"),
        ],
    )

    selection = resolve_selection(
        changes,
        (DisplayLineId(2), DisplayLineId(1), DisplayLineId(2)),
        view=_view(changes),
    )

    assert selection.display_ids.ranges() == ((1, 2),)
    assert len(selection.display_ids) == 2
    assert selection.display_ids.to_line_ranges().ranges() == ((1, 2),)

def test_second_hunk_insertion_uses_absolute_row_coordinates():
    """Synthetic file-view gaps do not become real coordinate increments."""
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(1, 10, 1, 12),
        [
            LineEntry(None, " ", 1, 1, text_bytes=b"first\n"),
            LineEntry(1, "+", None, 2, text_bytes=b"earlier\n"),
            LineEntry(None, " ", 2, 3, text_bytes=b"near\n"),
            LineEntry(None, " ", None, None, text_bytes=b"gap\n"),
            LineEntry(None, " ", 10, 11, text_bytes=b"later\n"),
            LineEntry(2, "+", None, 12, text_bytes=b"selected\n"),
        ],
    )
    view = diff_view_identity(
        changes,
        old_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", "old"), 10, DiffOldSpace
        ),
        new_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", "new"), 12, DiffNewSpace
        ),
    )

    unit = resolve_selection(
        changes,
        (DisplayLineId(2),),
        view=view,
    ).change_units[0]

    assert isinstance(unit, Insertion)
    assert unit.old_boundary.offset == 10


def test_semantic_selection_preserves_final_newline_identity():
    """Exact semantic bytes distinguish a final newline from its absence."""
    terminated = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"same", has_trailing_newline=True)],
    )
    unterminated = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, 1),
        [LineEntry(1, "+", None, 1, text_bytes=b"same", has_trailing_newline=False)],
    )

    terminated_unit = resolve_selection(
        terminated,
        (DisplayLineId(1),),
        view=diff_view_identity(
            terminated,
            old_snapshot=FileSnapshot(
                "file.txt", SnapshotIdentity("test", "empty"), 0, DiffOldSpace
            ),
            new_snapshot=FileSnapshot(
                "file.txt", SnapshotIdentity("test", "lf"), 1, DiffNewSpace
            ),
        ),
    ).change_units[0]
    unterminated_unit = resolve_selection(
        unterminated,
        (DisplayLineId(1),),
        view=diff_view_identity(
            unterminated,
            old_snapshot=FileSnapshot(
                "file.txt", SnapshotIdentity("test", "empty"), 0, DiffOldSpace
            ),
            new_snapshot=FileSnapshot(
                "file.txt", SnapshotIdentity("test", "no-lf"), 1, DiffNewSpace
            ),
        ),
    ).change_units[0]

    assert isinstance(terminated_unit, Insertion)
    assert isinstance(unterminated_unit, Insertion)
    assert terminated_unit.content == ExactContentWitness.from_lines((b"same\n",))
    assert unterminated_unit.content == ExactContentWitness.from_lines((b"same",))
    assert terminated_unit.content != unterminated_unit.content


def test_tiny_selection_heap_does_not_scale_with_unselected_hunk():
    """Semantic resolution scans context without retaining one object per row."""
    peaks: list[int] = []
    for context_count in (1024, 32768):
        lines = [
            LineEntry(None, " ", index, index, text_bytes=b"same\n")
            for index in range(1, context_count + 1)
        ]
        lines.append(
            LineEntry(
                1,
                "+",
                None,
                context_count + 1,
                text_bytes=b"selected\n",
            )
        )
        changes = LineLevelChange(
            "file.txt",
            HunkHeader(1, context_count, 1, context_count + 1),
            lines,
        )
        view = diff_view_identity(
            changes,
            old_snapshot=FileSnapshot(
                "file.txt",
                SnapshotIdentity("test", "old"),
                context_count,
                DiffOldSpace,
            ),
            new_snapshot=FileSnapshot(
                "file.txt",
                SnapshotIdentity("test", "new"),
                context_count + 1,
                DiffNewSpace,
            ),
        )
        gc.collect()
        tracemalloc.start()
        try:
            selection = resolve_selection(
                changes,
                (DisplayLineId(1),),
                view=view,
            )
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(selection.change_units) == 1
        peaks.append(peak)

    assert peaks[1] < peaks[0] + 16 * 1024


def test_contiguous_selection_heap_is_range_backed_and_bounded():
    """A large contiguous selection retains ranges and one semantic unit."""
    line_count = 32768
    changes = LineLevelChange(
        "file.txt",
        HunkHeader(0, 0, 1, line_count),
        [
            LineEntry(
                display_id,
                "+",
                None,
                display_id,
                text_bytes=b"selected",
            )
            for display_id in range(1, line_count + 1)
        ],
    )
    view = diff_view_identity(
        changes,
        old_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", "empty"), 0, DiffOldSpace
        ),
        new_snapshot=FileSnapshot(
            "file.txt", SnapshotIdentity("test", "populated"), line_count, DiffNewSpace
        ),
    )

    gc.collect()
    tracemalloc.start()
    try:
        selection = resolve_selection(
            changes,
            (DisplayLineId(display_id) for display_id in range(1, line_count + 1)),
            view=view,
        )
        retained, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert selection.display_ids.ranges() == ((1, line_count),)
    assert len(selection.change_units) == 1
    assert retained < 64 * 1024
    assert peak < 128 * 1024
