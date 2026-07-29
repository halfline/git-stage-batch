"""Typed application-facing batch metadata mappings."""

from __future__ import annotations

from typing import Literal, TypedDict

from ..ownership.metadata_types import BatchOwnershipMetadata




class BatchFileMetadataDict(BatchOwnershipMetadata, total=False):
    """Validated metadata for one path in a batch."""

    batch_source_commit: str
    change_type: Literal["added", "modified", "deleted"]
    mode: str






    source_path: str
