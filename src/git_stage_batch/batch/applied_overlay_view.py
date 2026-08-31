"""Describe applied batch state needed by merge and display code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from .applied_text_replay import AppliedTextApplication
from .ownership.attribution_metadata import (
    compact_ownership_metadata_for_attribution,
)
from .ownership.metadata_types import BatchOwnershipMetadata
from .state.metadata_types import BatchFileMetadataDict, BatchMetadataDict


@dataclass(frozen=True, slots=True)
class AppliedBatchOverlayView:
    """Applied batch details that are still valid for one file."""

    metadata_by_owner: dict[str, BatchMetadataDict]
    source_object_by_owner: dict[str, str]
    revealed_owner_names: frozenset[str]
    batch_names: frozenset[str]
    lifecycle_change_types: frozenset[str]
    applied_source_line_ranges_by_batch: dict[
        str,
        tuple[tuple[int, int], ...],
    ]
    source_line_ranges_by_batch: dict[str, tuple[tuple[int, int], ...]]
    index_preimage_source_line_ranges_by_batch: dict[
        str,
        tuple[tuple[int, int], ...],
    ]
    text_applications: tuple[AppliedTextApplication, ...] = ()

    @classmethod
    def empty(cls) -> AppliedBatchOverlayView:
        """Return a view with no applied batches."""
        return cls({}, {}, frozenset(), frozenset(), frozenset(), {}, {}, {})

    def contains_equivalent_file_provenance(
        self,
        file_path: str,
        file_metadata: BatchFileMetadataDict,
        source_object_id: str | None,
    ) -> bool:
        """Check whether the same batch selection is still applied."""
        if source_object_id is None:
            return False
        compact_metadata = applied_file_identity_metadata(file_metadata)
        return any(
            self.source_object_by_owner.get(owner_name) == source_object_id
            and applied_file_identity_metadata(
                owner_metadata.get("files", {}).get(file_path, {})
            )
            == compact_metadata
            for owner_name, owner_metadata in self.metadata_by_owner.items()
        )


def applied_file_identity_metadata(
    file_metadata: BatchFileMetadataDict,
) -> BatchFileMetadataDict:
    """Keep the fields that identify one applied selection."""
    compact = cast(
        BatchFileMetadataDict,
        compact_ownership_metadata_for_attribution(
            cast(BatchOwnershipMetadata, file_metadata)
        ),
    )
    source_commit = file_metadata.get("batch_source_commit")
    if source_commit is not None:
        compact["batch_source_commit"] = source_commit
    change_type = file_metadata.get("change_type")
    if change_type is not None:
        compact["change_type"] = change_type
    mode = file_metadata.get("mode")
    if mode is not None:
        compact["mode"] = mode
    return compact
