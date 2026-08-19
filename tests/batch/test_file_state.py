"""Tests for source-bound batch file state."""

from __future__ import annotations

import pytest

from git_stage_batch.batch.file_state import (
    BatchFileState,
    BatchMetadataRevision,
    SourceBoundOwnership,
)
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.claims import PresenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.ownership.references import BaselineReference
from git_stage_batch.batch.ownership.replacement_units import (
    ReplacementUnit,
    ReplacementUnitOrigin,
)
from git_stage_batch.core.coordinates import (
    BaselineSpace,
    BatchSourceSpace,
    content_snapshot,
)


def _snapshot(space, lines: tuple[bytes, ...]):
    return content_snapshot("file.txt", lines, space=space)


def _file_state(
    ownership: BatchOwnership,
    *,
    baseline_lines: tuple[bytes, ...] = (b"base\n",),
    source_lines: tuple[bytes, ...] = (b"source\n",),
) -> BatchFileState:
    source_snapshot = _snapshot(BatchSourceSpace, source_lines)
    return BatchFileState(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, baseline_lines),
        source_snapshot=source_snapshot,
        baseline_lines=baseline_lines,
        source_lines=source_lines,
        bound_ownership=SourceBoundOwnership(source_snapshot, ownership),
        metadata_revision=BatchMetadataRevision("metadata"),
    )


def test_batch_file_state_rejects_ownership_from_another_source():
    """Numerically valid ownership cannot exceed its bound source snapshot."""
    ownership = BatchOwnership.from_presence_lines(["2"])
    source_lines = (b"source\n",)
    source_snapshot = _snapshot(BatchSourceSpace, source_lines)

    with pytest.raises(ValueError, match="outside"):
        BatchFileState(
            path="file.txt",
            baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
            source_snapshot=source_snapshot,
            baseline_lines=(b"base\n",),
            source_lines=source_lines,
            bound_ownership=SourceBoundOwnership(source_snapshot, ownership),
            metadata_revision=BatchMetadataRevision("metadata"),
        )


def test_batch_file_state_rejects_runtime_snapshot_role_confusion():
    """Generic casts cannot turn baseline coordinates into source authority."""
    baseline_snapshot = _snapshot(BaselineSpace, (b"same\n",))

    with pytest.raises(ValueError, match="coordinate role"):
        SourceBoundOwnership(  # type: ignore[arg-type]
            baseline_snapshot,
            BatchOwnership.from_presence_lines(["1"]),
        )


def test_batch_file_state_rejects_untyped_ownership_and_revision():
    """Runtime adapters cannot bypass aggregate authority with plain values."""
    source_lines = (b"source\n",)
    source_snapshot = _snapshot(BatchSourceSpace, source_lines)
    arguments = {
        "path": "file.txt",
        "baseline_snapshot": _snapshot(BaselineSpace, (b"base\n",)),
        "source_snapshot": source_snapshot,
        "baseline_lines": (b"base\n",),
        "source_lines": source_lines,
    }

    with pytest.raises(TypeError, match="source-bound ownership"):
        BatchFileState(
            **arguments,
            bound_ownership=BatchOwnership.from_presence_lines(["1"]),  # type: ignore[arg-type]
            metadata_revision=BatchMetadataRevision("metadata"),
        )

    with pytest.raises(TypeError, match="metadata revision"):
        BatchFileState(
            **arguments,
            bound_ownership=SourceBoundOwnership(
                source_snapshot,
                BatchOwnership.from_presence_lines(["1"]),
            ),
            metadata_revision="metadata",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("revision", [1, b"metadata"])
def test_batch_metadata_revision_rejects_non_strings(revision: object):
    with pytest.raises(ValueError, match="non-empty"):
        BatchMetadataRevision(revision)  # type: ignore[arg-type]


def test_batch_file_state_rejects_same_sized_foreign_source_binding():
    """Numerically plausible ownership still carries its original source ID."""
    expected_source = _snapshot(BatchSourceSpace, (b"source-a\n",))
    foreign_source = _snapshot(BatchSourceSpace, (b"source-b\n",))
    bound = SourceBoundOwnership(
        foreign_source,
        BatchOwnership.from_presence_lines(["1"]),
    )

    with pytest.raises(ValueError, match="different source snapshot"):
        BatchFileState(
            path="file.txt",
            baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
            source_snapshot=expected_source,
            baseline_lines=(b"base\n",),
            source_lines=(b"source-a\n",),
            bound_ownership=bound,
            metadata_revision=BatchMetadataRevision("metadata"),
        )


def test_batch_file_state_rejects_same_sized_foreign_source_buffer():
    """The aggregate validates source bytes, not only ownership metadata."""
    source_snapshot = _snapshot(BatchSourceSpace, (b"expected\n",))

    with pytest.raises(ValueError, match="snapshots"):
        BatchFileState(
            path="file.txt",
            baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
            source_snapshot=source_snapshot,
            baseline_lines=(b"base\n",),
            source_lines=(b"different\n",),
            bound_ownership=SourceBoundOwnership(
                source_snapshot,
                BatchOwnership.from_presence_lines(["1"]),
            ),
            metadata_revision=BatchMetadataRevision("metadata"),
        )


def test_batch_file_state_revalidates_a_borrowed_buffer_before_use():
    """Mutation after construction cannot silently stale snapshot authority."""
    source_lines = [b"expected\n"]
    source_snapshot = content_snapshot(
        "file.txt",
        source_lines,
        space=BatchSourceSpace,
    )
    state = BatchFileState(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
        source_snapshot=source_snapshot,
        baseline_lines=(b"base\n",),
        source_lines=source_lines,
        bound_ownership=SourceBoundOwnership(
            source_snapshot,
            BatchOwnership.from_presence_lines(["1"]),
        ),
        metadata_revision=BatchMetadataRevision("metadata"),
    )
    source_lines[0] = b"different\n"

    with pytest.raises(ValueError, match="snapshots"):
        state.validate_content()


def test_batch_file_state_revalidates_mutable_ownership_before_use():
    """Compatibility metadata cannot mutate past aggregate validation."""
    source_lines = (b"source\n",)
    source_snapshot = content_snapshot(
        "file.txt",
        source_lines,
        space=BatchSourceSpace,
    )
    state = BatchFileState(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
        source_snapshot=source_snapshot,
        baseline_lines=(b"base\n",),
        source_lines=source_lines,
        bound_ownership=SourceBoundOwnership(
            source_snapshot,
            BatchOwnership.from_presence_lines(["1"]),
        ),
        metadata_revision=BatchMetadataRevision("metadata"),
    )
    state.ownership.presence_claims[0].source_lines = ["2"]

    with pytest.raises(ValueError, match="outside"):
        state.validate()


def test_source_advancement_rebinds_source_and_ownership_atomically():
    """Advancement returns a new aggregate instead of detachable fields."""
    source_lines = (b"source\n",)
    source_snapshot = _snapshot(BatchSourceSpace, source_lines)
    state = BatchFileState(
        path="file.txt",
        baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
        source_snapshot=source_snapshot,
        baseline_lines=(b"base\n",),
        source_lines=source_lines,
        bound_ownership=SourceBoundOwnership(
            source_snapshot,
            BatchOwnership.from_presence_lines(["1"]),
        ),
        metadata_revision=BatchMetadataRevision("metadata-1"),
    )

    advanced_source_lines = (b"source\n", b"new\n")
    advanced_source_snapshot = _snapshot(
        BatchSourceSpace,
        advanced_source_lines,
    )
    advanced = state.with_advanced_source(
        source_snapshot=advanced_source_snapshot,
        source_lines=advanced_source_lines,
        bound_ownership=SourceBoundOwnership(
            advanced_source_snapshot,
            BatchOwnership.from_presence_lines(["1-2"]),
        ),
        metadata_revision=BatchMetadataRevision("metadata-2"),
    )

    assert state.source_snapshot != advanced.source_snapshot
    assert advanced.ownership.presence_line_set().ranges() == ((1, 2),)


def test_batch_file_state_rejects_absence_anchor_from_another_source():
    """A deletion boundary cannot be detached from its source extent."""
    ownership = BatchOwnership(
        presence_claims=[],
        deletions=[AbsenceClaim(anchor_line=2, content_lines=(b"old\n",))],
    )
    source_lines = (b"source\n",)
    source_snapshot = _snapshot(BatchSourceSpace, source_lines)

    with pytest.raises(ValueError, match="absence anchor"):
        BatchFileState(
            path="file.txt",
            baseline_snapshot=_snapshot(BaselineSpace, (b"base\n",)),
            source_snapshot=source_snapshot,
            baseline_lines=(b"base\n",),
            source_lines=source_lines,
            bound_ownership=SourceBoundOwnership(source_snapshot, ownership),
            metadata_revision=BatchMetadataRevision("metadata"),
        )


@pytest.mark.parametrize("claimed_line", [True, "1"])
def test_batch_file_state_rejects_noninteger_presence_reference_line(
    claimed_line: object,
):
    """Compatibility objects cannot smuggle non-line keys into references."""
    ownership = BatchOwnership(
        presence_claims=[
            PresenceClaim(
                ["1"],
                {claimed_line: BaselineReference(after_line=1)},  # type: ignore[dict-item]
            )
        ],
        deletions=[],
    )

    with pytest.raises(ValueError, match="must be an integer"):
        _file_state(ownership)


@pytest.mark.parametrize("claimed_line", [0, -1, 2])
def test_batch_file_state_rejects_presence_reference_outside_source(
    claimed_line: int,
):
    ownership = BatchOwnership(
        presence_claims=[
            PresenceClaim(
                ["1"],
                {claimed_line: BaselineReference(after_line=1)},
            )
        ],
        deletions=[],
    )

    with pytest.raises(ValueError, match="outside its source snapshot"):
        _file_state(ownership)


def test_batch_file_state_rejects_reference_not_owned_by_its_claim():
    """A valid source coordinate cannot carry evidence for another claim."""
    ownership = BatchOwnership(
        presence_claims=[
            PresenceClaim(
                ["1"],
                {2: BaselineReference(after_line=1)},
            )
        ],
        deletions=[],
    )

    with pytest.raises(ValueError, match="not owned by its claim"):
        _file_state(ownership, source_lines=(b"one\n", b"two\n"))


@pytest.mark.parametrize(
    "deletion_indices",
    ([1], [-1], [True], [0, 0]),
)
def test_batch_file_state_rejects_invalid_replacement_deletion_indices(
    deletion_indices: list[int],
):
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=None, content_lines=(b"base\n",))],
        replacement_units=[ReplacementUnit(["1"], deletion_indices)],
    )

    with pytest.raises(ValueError, match="invalid deletion indices"):
        _file_state(ownership)


def test_batch_file_state_rejects_replacement_presence_outside_source():
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=None, content_lines=(b"base\n",))],
        replacement_units=[ReplacementUnit(["2"], [0])],
    )

    with pytest.raises(ValueError, match="presence is outside"):
        _file_state(ownership)


def test_batch_file_state_rejects_replacement_presence_not_owned_by_batch():
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=None, content_lines=(b"base\n",))],
        replacement_units=[ReplacementUnit(["2"], [0])],
    )

    with pytest.raises(ValueError, match="presence is not owned"):
        _file_state(ownership, source_lines=(b"one\n", b"two\n"))


def test_batch_file_state_rejects_replacement_origin_outside_source():
    ownership = BatchOwnership.from_presence_lines(
        ["1"],
        [AbsenceClaim(anchor_line=None, content_lines=(b"base\n",))],
        replacement_units=[
            ReplacementUnit(
                ["1"],
                [0],
                origin=ReplacementUnitOrigin(1, 1, 2, 2),
            )
        ],
    )

    with pytest.raises(ValueError, match="origin is outside its source"):
        _file_state(ownership)
