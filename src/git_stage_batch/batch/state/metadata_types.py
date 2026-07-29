"""Typed application-facing batch metadata mappings."""

from __future__ import annotations

from typing import Literal, TypedDict

from ..ownership.metadata_types import BatchOwnershipMetadata




class BatchFileMetadataDict(BatchOwnershipMetadata, total=False):
    """Validated metadata for one path in a batch."""

    batch_source_commit: str
    change_type: Literal["added", "modified", "deleted"]
    file_type: Literal["binary", "gitlink", "mode"]
    mode: str
    old_mode: str
    new_mode: str






    source_path: str
