"""Typed application-facing batch metadata mappings."""

from __future__ import annotations

from typing import Literal, TypedDict

from ..ownership.metadata_types import BatchOwnershipMetadata


class ReplacementMaskMetadata(TypedDict, total=False):
    """A consumed replacement signature stored for later filtering."""

    deleted_lines: list[str]
    added_lines: list[str]


class BatchFileMetadataDict(BatchOwnershipMetadata, total=False):
    """Validated metadata for one path in a batch."""

    batch_source_commit: str
    change_type: Literal["added", "modified", "deleted"]
    file_type: Literal["binary", "gitlink", "mode"]
    mode: str
    old_mode: str
    new_mode: str
    old_oid: str | None
    new_oid: str | None
    source_path: str
    replacement_masks: list[ReplacementMaskMetadata]
    legacy_unmarked_source_alternatives: bool


class BatchMetadataDict(TypedDict, total=False):
    """Validated application mapping for a complete batch."""

    schema_version: int
    revision: str | None
    batch: str
    note: str
    created_at: str
    baseline: str | None
    content_ref: str | None
    content_commit: str | None
    files: dict[str, BatchFileMetadataDict]


class BatchStorageMetadataDict(TypedDict):
    """Canonical current-schema mapping persisted as ``batch.json``."""

    schema_version: int
    revision: str
    batch: str
    note: str
    created_at: str
    baseline: str | None
    content_ref: str | None
    content_commit: str | None
    files: dict[str, BatchFileMetadataDict]


def add_ownership_metadata(
    file_metadata: BatchFileMetadataDict,
    ownership_metadata: BatchOwnershipMetadata,
) -> None:
    """Copy ownership fields into application-facing file metadata."""
    if "presence_claims" in ownership_metadata:
        file_metadata["presence_claims"] = ownership_metadata["presence_claims"]
    if "claimed_lines" in ownership_metadata:
        file_metadata["claimed_lines"] = ownership_metadata["claimed_lines"]
    if "deletions" in ownership_metadata:
        file_metadata["deletions"] = ownership_metadata["deletions"]
    if "replacement_units" in ownership_metadata:
        file_metadata["replacement_units"] = ownership_metadata["replacement_units"]
